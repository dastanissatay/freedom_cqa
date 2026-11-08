"""Детерминированный расчёт баллов. LLM не участвует.

Порядок (по ТЗ):
1. Применимость: N/A только там, где разрешён.
2. «Да» = полный вес, «Нет» = 0; шкала — одна из заданных ступеней.
3. Баллы шкалы = ступень / максимум_шкалы × вес.
4. Итог = сумма_набранных / сумма_весов_применимых × 100.
5. Округление до 2 знаков; нет применимых критериев с весом → None.
   Критерии типа flag (грубые ошибки) не влияют на баллы.
Аналогично для каждой секции.
"""
from __future__ import annotations

from typing import Any, Mapping

from .schemas import Answer, ChecklistConfig, Criterion


class ScoringError(ValueError):
    pass


def _round2(x: float) -> float:
    # round half up, чтобы 85.385 → 85.39, а не банковское округление
    from decimal import ROUND_HALF_UP, Decimal

    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def check_answer(crit: Criterion, answer: Answer) -> None:
    """Бросает ScoringError, если ответ недопустим для критерия."""
    if answer == "na":
        if not crit.na_allowed:
            raise ScoringError(f"Критерий {crit.id}: N/A не разрешён")
        return
    if crit.type == "scale":
        if isinstance(answer, bool) or not isinstance(answer, int):
            raise ScoringError(f"Критерий {crit.id}: ожидается ступень шкалы, получено {answer!r}")
        if answer not in [l.value for l in crit.scale]:
            raise ScoringError(f"Критерий {crit.id}: ступени {answer} нет в шкале")
        return
    if answer not in ("yes", "no"):
        raise ScoringError(f"Критерий {crit.id}: ожидается yes/no, получено {answer!r}")


def criterion_points(crit: Criterion, answer: Answer) -> float | None:
    """Баллы за критерий; None — если N/A или flag."""
    check_answer(crit, answer)
    if crit.type == "flag" or answer == "na":
        return None
    if crit.type == "scale":
        return answer / crit.scale_max * crit.weight  # type: ignore[operator]
    return crit.weight if answer == "yes" else 0.0


def compute(config: ChecklistConfig, answers: Mapping[str, Answer]) -> dict[str, Any]:
    """answers: {criterion_id: answer}. Все критерии чек-листа должны иметь ответ."""
    missing = [c.id for c in config.criteria if c.id not in answers]
    if missing:
        raise ScoringError(f"Нет ответа по критериям: {', '.join(missing)}")

    crit_rows = []
    flags = []
    for c in config.criteria:
        a = answers[c.id]
        pts = criterion_points(c, a)
        applicable = c.type != "flag" and a != "na"
        if c.type == "flag" and a == "yes":
            flags.append(c.id)
        crit_rows.append(
            {
                "criterion_id": c.id,
                "section_id": c.section_id,
                "type": c.type,
                "answer": a,
                "applicable": applicable,
                "weight": c.weight,
                "points": None if pts is None else _round2(pts),
                "_raw": pts,
            }
        )

    def aggregate(rows):
        earned = sum(r["_raw"] for r in rows if r["applicable"])
        weight = sum(r["weight"] for r in rows if r["applicable"])
        score = _round2(earned / weight * 100) if weight > 0 else None
        return _round2(earned), _round2(weight), score

    sections = []
    for s in config.sections:
        rows = [r for r in crit_rows if r["section_id"] == s.id]
        earned, weight, score = aggregate(rows)
        sections.append(
            {
                "section_id": s.id,
                "title": s.title,
                "earned": earned,
                "applicable_weight": weight,
                "max_weight": _round2(sum(r["weight"] for r in rows if r["type"] != "flag")),
                "score": score,
                "flags": [r["criterion_id"] for r in rows if r["criterion_id"] in flags],
            }
        )

    earned, weight, total = aggregate(crit_rows)
    for r in crit_rows:
        r.pop("_raw")
    return {
        "total": total,
        "earned": earned,
        "applicable_weight": weight,
        "critical_flag": bool(flags),
        "critical_criteria": flags,
        "criteria": crit_rows,
        "sections": sections,
    }
