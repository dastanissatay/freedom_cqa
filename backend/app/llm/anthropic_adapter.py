"""Claude через официальный SDK anthropic.

- Structured outputs: output_config.format = json_schema (схема строится из чек-листа).
  Если API отклонит схему, повторяем запрос без неё: формат задан в промпте, ответ проверяет validation.py.
- temperature не передаётся: на актуальных моделях Claude параметр удалён (API вернёт 400).
- Глубина рассуждений — output_config.effort (LLM_EFFORT).
"""
from __future__ import annotations

from typing import Any, Optional

import anthropic

from .base import LLMAdapter, LLMError, LLMResponse


class AnthropicAdapter(LLMAdapter):
    provider = "anthropic"

    def __init__(self, model: str, api_key: str, effort: str, max_tokens: int):
        super().__init__(model)
        self.client = anthropic.Anthropic(api_key=api_key or None, max_retries=3, timeout=300)
        self.effort = effort
        self.max_tokens = max_tokens
        self.use_schema = True

    def _call(self, system: str, user: str, schema: dict):
        output_config: dict[str, Any] = {"effort": self.effort}
        if self.use_schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        return self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            # статическая часть (инструкции + чек-лист) — в system с кэшированием
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config=output_config,
        )

    def complete(self, system: str, user: str, schema: dict, context: Optional[dict] = None) -> LLMResponse:
        try:
            try:
                resp = self._call(system, user, schema)
            except anthropic.BadRequestError as e:
                msg = str(e).lower()
                if not (self.use_schema and any(k in msg for k in ("grammar", "schema", "output_config", "format"))):
                    raise
                self.use_schema = False  # схему не приняли — дальше работаем без неё
                resp = self._call(system, user, schema)
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
