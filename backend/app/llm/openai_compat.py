"""OpenAI-совместимый API: vLLM / Ollama / LM Studio / OpenRouter (своя модель на арендованных GPU).

temperature = 0. Сначала пробуем response_format=json_schema (guided decoding в vLLM),
если сервер не поддерживает — json_object, затем без формата (валидация всё равно в коде).
"""
from __future__ import annotations

from typing import Optional

import openai

from .base import LLMAdapter, LLMError, LLMResponse


class OpenAICompatAdapter(LLMAdapter):
    provider = "openai_compat"

    def __init__(self, model: str, base_url: str, api_key: str, max_tokens: int):
        super().__init__(model)
        self.client = openai.OpenAI(base_url=base_url, api_key=api_key or "EMPTY", timeout=600, max_retries=2)
        self.max_tokens = max_tokens
        self._format_mode = "json_schema"

    def _formats(self, schema: dict):
        modes = ["json_schema", "json_object", "none"]
        for m in modes[modes.index(self._format_mode):]:
            if m == "json_schema":
                yield m, {"type": "json_schema", "json_schema": {"name": "cqa_verdicts", "schema": schema, "strict": True}}
            elif m == "json_object":
                yield m, {"type": "json_object"}
            else:
                yield m, None

    def complete(self, system: str, user: str, schema: dict, context: Optional[dict] = None) -> LLMResponse:
        last_err: Exception | None = None
        for mode, fmt in self._formats(schema):
            kwargs = dict(
                model=self.model,
                temperature=0,
                max_tokens=self.max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
            if fmt:
                kwargs["response_format"] = fmt
            try:
                resp = self.client.chat.completions.create(**kwargs)
            except openai.BadRequestError as e:
                last_err = e
                continue  # сервер не поддерживает этот response_format — пробуем проще
            except openai.AuthenticationError as e:
                raise LLMError(f"Ошибка авторизации OpenAI-совместимого API: {e}", retryable=False) from e
            except openai.APIError as e:
                raise LLMError(f"Ошибка OpenAI-совместимого API: {e}") from e
            self._format_mode = mode
            choice = resp.choices[0]
            if choice.finish_reason == "length":
                raise LLMError("Ответ обрезан по max_tokens")
            u = resp.usage
            usage = {
                "input_tokens": getattr(u, "prompt_tokens", 0) or 0,
                "output_tokens": getattr(u, "completion_tokens", 0) or 0,
            }
            return LLMResponse(text=choice.message.content or "", model=resp.model or self.model, usage=usage,
                               stop_reason=choice.finish_reason)
        raise LLMError(f"Сервер отклонил запрос: {last_err}", retryable=False)
