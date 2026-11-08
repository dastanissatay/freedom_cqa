"""Детерминированный адаптер без сети: для тестов и демо без API-ключа.

Не претендует на качество — простые эвристики, чтобы прогнать весь контур.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from .base import LLMAdapter, LLMResponse

GREET = re.compile(r"здравств|добрый|привет|hello|hi\b|good (morning|afternoon|evening)|сәлем|қайырлы|салем", re.I)
BYE = re.compile(r"до свидан|всего доброго|хорошего|goodbye|bye|have a (nice|good)|сау болыңыз|көмектесе|еще вопрос|ещё вопрос|anything else", re.I)


class MockAdapter(LLMAdapter):
    provider = "mock"

    def complete(self, system: str, user: str, schema: dict, context: Optional[dict] = None) -> LLMResponse:
        cfg = context["checklist"]
        msgs = context["transcript"]
        n = len(msgs)
        ops = [m for m in msgs if m["speaker"] == "operator"]
        first_op = ops[0]["index"] if ops else 0
        last_op = ops[-1]["index"] if ops else max(0, n - 1)
        out = {}
        for c in cfg.criteria:
            ev = [first_op] if ops else []
            rng = None
            if c.type == "flag":
                ans, expl = "no", "Мок: грубых ошибок не обнаружено."
                ev = []
            elif c.type == "scale":
                ans, expl = c.scale_max, f"Мок: выставлена максимальная ступень {c.scale_max}."
            elif c.id == "01":
                ok = bool(ops) and bool(GREET.search(ops[0]["text"]))
                ans = "yes" if ok else "no"
                expl = "Мок: приветствие найдено в первой реплике оператора." if ok else "Мок: приветствия в первой реплике нет."
                rng = None if ok else [first_op, first_op]
            elif c.id == "07":
                ok = bool(ops) and bool(BYE.search(ops[-1]["text"]))
                ans = "yes" if ok else "no"
                ev = [last_op]
                expl = "Мок: оператор попрощался/предложил помощь." if ok else "Мок: прощания в конце нет."
                rng = None if ok else [last_op, max(last_op, n - 1)]
            elif c.na_allowed and c.id in ("02", "03"):
                ans, expl, ev = "na", f"Мок: условие N/A — {c.na_condition}", []
            else:
                ans, expl = "yes", "Мок: требование выполнено."
            out[c.id] = {"evidence": ev, "checked_range": rng, "explanation": expl, "answer": ans}
        text = json.dumps({"verdicts": out}, ensure_ascii=False)
        return LLMResponse(text=text, model="mock", usage={"input_tokens": len(system + user) // 4,
                                                           "output_tokens": len(text) // 4})
