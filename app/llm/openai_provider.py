"""OpenAI-compatible chat-completions provider (any compatible endpoint).

Not exercised in automated tests. Enable with ``LLM_PROVIDER=openai``,
``LLM_API_KEY``, ``LLM_MODEL`` and optionally ``LLM_BASE_URL``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import get_settings
from app.llm.base import LLMError, LLMResponse, ToolCall


class OpenAICompatibleLLM:
    name = "openai"

    def __init__(self) -> None:
        s = get_settings()
        if not s.llm_model:
            raise LLMError("LLM_MODEL_REQUIRED", "LLM_MODEL must be set for LLM_PROVIDER=openai")
        self.base = (s.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        self.model = s.llm_model
        self.key = s.llm_api_key
        self.timeout = s.llm_timeout_seconds

    def chat(
        self, *, agent: str, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse:
        conv: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "tool":
                conv.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            elif m["role"] == "assistant":
                entry: dict[str, Any] = {"role": "assistant", "content": m["content"] or None}
                if m.get("tool_calls"):
                    entry["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                        }
                        for c in m["tool_calls"]
                    ]
                conv.append(entry)
            else:
                conv.append({"role": "user", "content": m["content"]})
        body: dict[str, Any] = {"model": self.model, "messages": conv}
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]},
                }
                for t in tools
            ]
        try:
            r = httpx.post(
                f"{self.base}/chat/completions",
                json=body,
                timeout=self.timeout,
                headers={"Authorization": f"Bearer {self.key}"},
            )
        except httpx.HTTPError as exc:
            raise LLMError("LLM_UNREACHABLE", "The LLM provider is unreachable.", retryable=True) from exc
        if r.status_code in (401, 403):
            raise LLMError("LLM_AUTH_FAILED", "The LLM provider rejected the API key.")
        if r.status_code == 429:
            raise LLMError("LLM_RATE_LIMITED", "The LLM provider rate limit or quota was hit.", retryable=True)
        if r.status_code >= 400:
            raise LLMError("LLM_PROVIDER_ERROR", f"LLM provider error {r.status_code}", retryable=r.status_code >= 500)
        data = r.json()
        msg = data["choices"][0]["message"]
        calls = [
            ToolCall(id=c["id"], name=c["function"]["name"], arguments=json.loads(c["function"]["arguments"] or "{}"))
            for c in msg.get("tool_calls") or []
        ]
        usage = data.get("usage", {})
        return LLMResponse(
            text=msg.get("content") or "",
            tool_calls=calls,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            model=data.get("model", self.model),
            stop_reason=data["choices"][0].get("finish_reason", ""),
        )
