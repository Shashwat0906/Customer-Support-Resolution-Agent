"""Anthropic Claude provider (official SDK).

Not exercised in this repository's automated tests (no API key in CI); the
deterministic mock provider is the default. Enable with
``LLM_PROVIDER=anthropic`` and ``LLM_API_KEY``.
"""

from __future__ import annotations

import json
from typing import Any

from app.config import get_settings
from app.llm.base import LLMError, LLMResponse, ToolCall

DEFAULT_MODEL = "claude-opus-5-5"


class AnthropicLLM:
    name = "anthropic"

    def __init__(self) -> None:
        try:
            import anthropic
        except ImportError as exc:  # optional dependency
            raise LLMError("LLM_SDK_MISSING", "Install the 'anthropic' extra to use LLM_PROVIDER=anthropic") from exc
        settings = get_settings()
        self._anthropic = anthropic
        self.model = settings.llm_model or DEFAULT_MODEL
        kwargs: dict[str, Any] = {
            "api_key": settings.llm_api_key,
            "timeout": settings.llm_timeout_seconds,
            "max_retries": 2,
        }
        if settings.llm_base_url:
            kwargs["base_url"] = settings.llm_base_url
        self.client = anthropic.Anthropic(**kwargs)

    @staticmethod
    def _convert(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        pending_results: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "tool":
                pending_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": m["tool_call_id"],
                        "content": m["content"],
                        "is_error": bool(m.get("is_error")),
                    }
                )
                continue
            if pending_results:  # all results of one turn go back in a single user message
                out.append({"role": "user", "content": pending_results})
                pending_results = []
            if m["role"] == "assistant":
                # Pass the provider's content blocks back unchanged (keeps thinking blocks valid).
                out.append({"role": "assistant", "content": m.get("provider_content") or m["content"]})
            else:
                out.append({"role": "user", "content": m["content"]})
        if pending_results:
            out.append({"role": "user", "content": pending_results})
        return out

    def chat(
        self, *, agent: str, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse:
        settings = get_settings()
        a = self._anthropic
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": settings.llm_max_tokens,
            "system": system,
            "messages": self._convert(messages),
            "output_config": {"effort": settings.llm_effort},
            # Server-side refusal fallback, routed by refusal category.
            "betas": ["server-side-fallback-2026-07-01"],
            "fallbacks": "default",
        }
        if tools:
            request["tools"] = tools
        try:
            response = self.client.beta.messages.create(**request)
        except a.AuthenticationError as exc:
            raise LLMError("LLM_AUTH_FAILED", "The LLM provider rejected the API key.") from exc
        except a.PermissionDeniedError as exc:
            raise LLMError("LLM_PERMISSION_DENIED", "The API key lacks permission for this model.") from exc
        except a.RateLimitError as exc:
            raise LLMError("LLM_RATE_LIMITED", "The LLM provider rate limit or quota was hit.", retryable=True) from exc
        except a.BadRequestError as exc:
            raise LLMError("LLM_BAD_REQUEST", f"The LLM provider rejected the request: {exc.message}") from exc
        except a.APIStatusError as exc:
            raise LLMError(
                "LLM_PROVIDER_ERROR", f"LLM provider error {exc.status_code}", retryable=exc.status_code >= 500
            ) from exc
        except a.APIConnectionError as exc:
            raise LLMError("LLM_UNREACHABLE", "The LLM provider is unreachable.", retryable=True) from exc
        if response.stop_reason == "refusal":
            raise LLMError("LLM_REFUSAL", "The model declined this request.")
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                args = block.input if isinstance(block.input, dict) else json.loads(block.input or "{}")
                calls.append(ToolCall(id=block.id, name=block.name, arguments=args))
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=calls,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=response.model,
            stop_reason=response.stop_reason or "",
            provider_content=[b.model_dump(exclude_none=True) for b in response.content],
        )
