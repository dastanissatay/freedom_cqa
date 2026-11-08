"""Фабрика адаптеров: LLM_PROVIDER = anthropic | openai_compat | mock."""
from __future__ import annotations

from functools import lru_cache

from ..config import get_settings
from .base import LLMAdapter, LLMError, LLMResponse

__all__ = ["get_adapter", "LLMAdapter", "LLMError", "LLMResponse"]


@lru_cache
def get_adapter() -> LLMAdapter:
    s = get_settings()
    p = s.llm_provider.lower()
    if p == "anthropic":
        from .anthropic_adapter import AnthropicAdapter

        return AnthropicAdapter(s.llm_model, s.anthropic_api_key, s.llm_effort, s.llm_max_tokens)
    if p in ("openai_compat", "openai", "vllm", "ollama"):
        from .openai_compat import OpenAICompatAdapter

        return OpenAICompatAdapter(s.llm_model, s.openai_base_url, s.openai_api_key, s.llm_max_tokens)
    if p == "mock":
        from .mock import MockAdapter

        return MockAdapter("mock")
    raise ValueError(f"Неизвестный LLM_PROVIDER: {s.llm_provider}")
