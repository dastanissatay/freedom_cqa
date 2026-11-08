"""Разбор и проверка ответа LLM против чек-листа и транскрипта."""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from .schemas import ChecklistConfig, Verdict
from .scoring import ScoringError, check_answer


def extract_json(text: str) -> Any:
    s = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if fence:
        s = fence.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end > start:
            return json.loads(s[start : end + 1])
        raise


def _to_list(data: Any) -> list[dict]:
    """Принимает {"verdicts": {id: {...}}} или {"verdicts": [{criterion_id, ...}]}."""
    v = data.get("verdicts", data) if isinstance(data, dict) else data
    if isinstance(v, dict):
        return [{"criterion_id": k, **(val if isinstance(val, dict) else {})} for k, val in v.items()]
    if isinstance(v, list):
        return v
    raise ValueError("verdicts должен быть объектом или массивом")


def parse_and_validate(
    text: str, cfg: ChecklistConfig, n_messages: int
) -> tuple[list[Verdict], list[str], list[str]]:
    """Возвращает (вердикты по порядку чек-листа, ошибки → нужен повтор, предупреждения)."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        items = _to_list(extract_json(text))
    except (ValueError, json.JSONDecodeError) as e:
        return [], [f"Ответ не является корректным JSON: {e}"], []

    by_id: dict[str, Verdict] = {}
    for raw in items:
        try:
            v = Verdict.model_validate(raw)
        except ValidationError as e:
            errors.append(f"Критерий {raw.get('criterion_id')}: некорректная структура ({e.errors()[0]['msg']})")
            continue
        if cfg.criterion(v.criterion_id) is None:
            warnings.append(f"Лишний критерий {v.criterion_id} проигнорирован")
            continue
        if v.criterion_id in by_id:
            errors.append(f"Критерий {v.criterion_id} указан дважды")
            continue
        by_id[v.criterion_id] = v

    result: list[Verdict] = []
    for c in cfg.criteria:
        v = by_id.get(c.id)
        if v is None:
            errors.append(f"Нет вердикта по критерию {c.id}")
            continue
        try:
            check_answer(c, v.answer)
        except ScoringError as e:
            errors.append(f"{e}. Допустимо: {c.allowed_answers()}")
            continue

        bad = [i for i in v.evidence if not (0 <= i < n_messages)]
        if bad:
            warnings.append(f"Критерий {c.id}: несуществующие номера реплик {bad} удалены")
            v.evidence = [i for i in v.evidence if 0 <= i < n_messages]
        v.evidence = sorted(set(v.evidence))

        if v.checked_range is not None and len(v.checked_range) == 0:
            v.checked_range = None  # [] = участок не указан
        if v.checked_range is not None:
            r = v.checked_range
            if len(r) == 1:
                r = [r[0], r[0]]
            if len(r) != 2:
                warnings.append(f"Критерий {c.id}: checked_range должен быть [начало, конец] — удалён")
                v.checked_range = None
            else:
                a, b = sorted(r)
                a, b = max(0, a), min(n_messages - 1, b)
                v.checked_range = [a, b] if a <= b else None
                if v.checked_range is None:
                    warnings.append(f"Критерий {c.id}: checked_range вне диапазона — удалён")

        if not v.explanation.strip():
            errors.append(f"Критерий {c.id}: пустое объяснение")
            continue

        missed = c.type != "flag" and (v.answer == "no" or (c.type == "scale" and v.answer != "na" and v.answer < c.scale_max))
        if missed and not v.evidence and not v.checked_range:
            errors.append(
                f"Критерий {c.id}: ответ {v.answer!r} без доказательств — укажи evidence или checked_range (где ожидалось действие)"
            )
            continue
        result.append(v)
    return result, errors, warnings
