"""LLM provider selection from configuration (LLM_PROVIDER)."""

from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.llm.base import LLMClient


@lru_cache
def get_llm() -> LLMClient:
    provider = get_settings().llm_provider
    if provider == "anthropic":
        from app.llm.anthropic_provider import AnthropicLLM

        return AnthropicLLM()
    if provider == "openai":
        from app.llm.openai_provider import OpenAICompatibleLLM

        return OpenAICompatibleLLM()
    from app.llm.mock import MockLLM

    return MockLLM()
