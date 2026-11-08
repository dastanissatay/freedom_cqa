"""Claude через официальный SDK anthropic.

- Structured outputs: output_config.format = json_schema (схема строится из чек-листа).
- temperature не передаётся: на актуальных моделях Claude параметр удалён (API вернёт 400).
  Стабильность обеспечивают строгая схема, валидация в коде и повтор.
- Глубина рассуждений — output_config.effort (LLM_EFFORT).
- Server-side fallback при отказе (refusal) включён по умолчанию (бета), при 400 — запрос без него.
"""
from __future__ import annotations

import inspect
from typing import Any, Optional

import anthropic

from .base import LLMAdapter, LLMError, LLMResponse

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicAdapter(LLMAdapter):
    provider = "anthropic"

    def __init__(self, model: str, api_key: str, effort: str, max_tokens: int, use_fallback: bool = True):
        super().__init__(model)
        self.client = anthropic.Anthropic(api_key=api_key or None, max_retries=3, timeout=300)
        self.effort = effort
        self.max_tokens = max_tokens
        self.use_fallback = use_fallback
        # structured outputs; если API отклонит схему (слишком большая грамматика и т.п.) —
        # работаем без неё: формат задан в промпте, ответ всё равно проверяет validation.py
        self.use_schema = True
        self._native_output_config = "output_config" in inspect.signature(self.client.messages.create).parameters

    def _call(self, system: str, user: str, schema: dict, fallback: bool):
        output_config: dict[str, Any] = {"effort": self.effort}
        if self.use_schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            # статическая часть (инструкции + чек-лист) — в system с кэшированием
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        extra_body: dict[str, Any] = {}
        if self._native_output_config:
            kwargs["output_config"] = output_config
        else:
            extra_body["output_config"] = output_config
        if fallback:
            extra_body["fallbacks"] = "default"
            kwargs["extra_headers"] = {"anthropic-beta": FALLBACK_BETA}
        if extra_body:
            kwargs["extra_body"] = extra_body
        return self.client.messages.create(**kwargs)

    def complete(self, system: str, user: str, schema: dict, context: Optional[dict] = None) -> LLMResponse:
        try:
            for _ in range(3):  # деградация: без беты fallback → без схемы
                try:
                    resp = self._call(system, user, schema, self.use_fallback)
                    break
                except anthropic.BadRequestError as e:
                    msg = str(e).lower()
                    if self.use_fallback and "fallback" in msg:
                        self.use_fallback = False  # бета недоступна — работаем без неё
                    elif self.use_schema and any(k in msg for k in ("grammar", "schema", "output_config", "format")):
                        self.use_schema = False
                    else:
                        raise
            else:
                raise LLMError("Claude отклонил запрос даже в упрощённом режиме", retryable=False)
        except anthropic.AuthenticationError as e:
            raise LLMError(f"Неверный ANTHROPIC_API_KEY: {e}", retryable=False) from e
        except anthropic.PermissionDeniedError as e:
            raise LLMError(f"Нет доступа к модели {self.model}: {e}", retryable=False) from e
        except anthropic.NotFoundError as e:
            raise LLMError(f"Модель {self.model} не найдена: {e}", retryable=False) from e
        except anthropic.BadRequestError as e:
            raise LLMError(f"Некорректный запрос к Claude: {e}", retryable=False) from e
        except anthropic.RateLimitError as e:
            raise LLMError(f"Лимит запросов Claude: {e}") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Ошибка Claude API {e.status_code}: {e}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"Нет соединения с Claude API: {e}") from e

        u = resp.usage
        usage = {
            "input_tokens": getattr(u, "input_tokens", 0) or 0,
            "output_tokens": getattr(u, "output_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
        }
        if resp.stop_reason == "refusal":
            raise LLMError("Модель отказалась отвечать (refusal)")
        if resp.stop_reason == "max_tokens":
            raise LLMError("Ответ обрезан по max_tokens — увеличьте LLM_MAX_TOKENS")
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        return LLMResponse(text=text, model=getattr(resp, "model", self.model), usage=usage, stop_reason=resp.stop_reason)
