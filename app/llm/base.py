"""Provider-neutral LLM interface.

Messages use a neutral shape so agents are provider independent:
    {"role": "user", "content": str}
    {"role": "assistant", "content": str, "tool_calls": [ToolCall...], "provider_content": Any}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str, "is_error": bool}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.config import get_settings


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    stop_reason: str = "end_turn"
    provider_content: Any = None


class LLMError(Exception):
    """Provider failure (auth, quota, network). ``retryable`` marks transient ones."""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class LLMClient(Protocol):
    name: str

    def chat(
        self, *, agent: str, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse: ...


def estimate_cost_usd(input_tokens: int, output_tokens: int) -> float:
    s = get_settings()
    return (input_tokens * s.llm_input_cost_per_mtok + output_tokens * s.llm_output_cost_per_mtok) / 1_000_000
