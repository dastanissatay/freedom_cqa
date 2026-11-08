"""Общий интерфейс LLM-адаптеров. Модель меняется настройкой LLM_PROVIDER / LLM_MODEL."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


class LLMError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class LLMResponse:
    text: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)  # input_tokens, output_tokens, cache_*
    stop_reason: Optional[str] = None


class LLMAdapter:
    provider: str = "base"

    def __init__(self, model: str):
        self.model = model

    def complete(self, system: str, user: str, schema: dict, context: Optional[dict[str, Any]] = None) -> LLMResponse:
        raise NotImplementedError
