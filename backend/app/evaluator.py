"""Оценка одного диалога: промпт → LLM (1 вызов на все критерии) → валидация/повтор → расчёт → сохранение."""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, HTTPException

from . import db, scoring
from .checklists import get_version, load_config
from .config import get_settings
from .dataset import get_conversation
from .llm import LLMError, get_adapter
from .llm.pricing import cost_usd
from .prompt import build_schema, build_system, build_user
from .schemas import EvaluateRequest
from .validation import parse_and_validate

router = APIRouter(prefix="/api/evaluations", tags=["evaluations"])


class EvaluationFailed(Exception):
    pass


def run_llm(conv: dict, cfg) -> dict:
    """Один прогон оценки: промпт → LLM → валидация (с повтором при ошибке). Ничего не сохраняет."""
    adapter = get_adapter()
    settings = get_settings()
    system = build_system(cfg)
    schema = build_schema(cfg)
    n = len(conv["transcript"])
    if n == 0:
        raise EvaluationFailed("Пустой транскрипт")

    usage: dict[str, int] = {}
    errors: list[str] = []
    warnings: list[str] = []
    model = adapter.model
    started = time.time()
    for attempt in range(settings.llm_max_retries + 1):
        user = build_user(conv, errors or None, mask_pii=settings.pii_masking)
        try:
            resp = adapter.complete(system, user, schema, context={"checklist": cfg, "transcript": conv["transcript"]})
        except LLMError as e:
            if not e.retryable:
                raise EvaluationFailed(str(e)) from e
            errors = [f"Сбой вызова модели: {e}"]
            time.sleep(min(2 ** attempt, 8))
            continue
        model = resp.model or model
        for k, v in resp.usage.items():
            usage[k] = usage.get(k, 0) + int(v or 0)
        parsed, errors, warnings = parse_and_validate(resp.text, cfg, n)
        if not errors:
            cost = cost_usd(adapter.model if adapter.provider == "mock" else model, usage)
            if cost is None:
                cost = cost_usd(adapter.model, usage)
            usage["latency_sec"] = round(time.time() - started, 2)
            usage["warnings"] = warnings  # type: ignore[assignment]
            return {"verdicts": parsed, "usage": usage, "model": model, "provider": adapter.provider,
                    "attempts": attempt + 1, "cost": cost}
    raise EvaluationFailed("Модель не вернула корректный ответ: " + "; ".join(errors[:5]))


def evaluate(group_id: str, conversation_id: str, checklist_version_id: int) -> dict:
    conv = get_conversation(group_id, conversation_id)
    cfg = load_config(checklist_version_id)
    run = run_llm(conv, cfg)
    result = scoring.compute(cfg, {v.criterion_id: v.answer for v in run["verdicts"]})
    eid = db.execute(
        """INSERT INTO evaluations(group_id, conversation_id, checklist_version_id, provider, model,
             verdicts_json, result_json, usage_json, cost_usd, attempts, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (group_id, conversation_id, checklist_version_id, run["provider"], run["model"],
         db.dumps([v.model_dump() for v in run["verdicts"]]), db.dumps(result), db.dumps(run["usage"]),
         run["cost"], run["attempts"], db.now()),
    )
    return get_evaluation(eid)


def check_stability(eid: int, runs: int = 1) -> dict:
    """Повторяет оценку того же диалога тем же чек-листом и сравнивает ответы по критериям.

    Повторы не становятся новой оценкой: результат сохраняется в usage оценки (ключ "stability").
    Аналог калибровочной сессии: критерии, где ответы расходятся, сформулированы неоднозначно
    или диалог пограничный — их стоит проверить вручную.
    """
    ev = db.query_one("SELECT * FROM evaluations WHERE id=?", (eid,))
    if not ev:
        raise HTTPException(404, "Оценка не найдена")
    cfg = load_config(ev["checklist_version_id"])
    conv = get_conversation(ev["group_id"], ev["conversation_id"])
    base = {v["criterion_id"]: v["answer"] for v in json.loads(ev["verdicts_json"])}
    answers = {cid: [a] for cid, a in base.items()}
    cost = 0.0
    for _ in range(max(1, min(runs, 3))):
        run = run_llm(conv, cfg)
        cost += run["cost"] or 0
        for v in run["verdicts"]:
            answers.setdefault(v.criterion_id, []).append(v.answer)
    criteria = [{"criterion_id": c.id, "answers": answers.get(c.id, []),
                 "stable": len({json.dumps(a) for a in answers.get(c.id, [])}) == 1} for c in cfg.criteria]
    stable = sum(c["stable"] for c in criteria)
    report = {"runs": len(next(iter(answers.values()))) - 1, "stable": stable, "total": len(criteria),
              "agreement_pct": round(stable / len(criteria) * 100, 1), "criteria": criteria,
              "cost_usd": round(cost, 6), "checked_at": db.now()}
    usage = json.loads(ev["usage_json"] or "{}")
    usage["stability"] = report
    db.execute("UPDATE evaluations SET usage_json=? WHERE id=?", (db.dumps(usage), eid))
    return report


def get_evaluation(eid: int) -> dict:
    from .appeals import final_result_for

    row = db.query_one("SELECT * FROM evaluations WHERE id=?", (eid,))
    if not row:
        raise HTTPException(404, "Оценка не найдена")
    row["verdicts"] = json.loads(row.pop("verdicts_json"))
    row.pop("result_json")
    row["usage"] = json.loads(row.pop("usage_json") or "{}")
    ver = get_version(row["checklist_version_id"])
    row["checklist"] = {"version_id": ver["id"], "checklist_id": ver["checklist_id"], "name": ver["checklist_name"],
                        "version": ver["version"], "config": ver["config"]}
    row.update(final_result_for(eid))
    return row


# ---------- API ----------

@router.post("")
def api_evaluate(body: EvaluateRequest):
    try:
        return evaluate(body.group_id, body.conversation_id, body.checklist_version_id)
    except EvaluationFailed as e:
        raise HTTPException(502, str(e)) from e


@router.get("")
def api_list(group_id: str, conversation_id: str):
    return db.query(
        """SELECT e.id, e.checklist_version_id, e.model, e.cost_usd, e.created_at, v.version, c.name AS checklist_name
           FROM evaluations e JOIN checklist_versions v ON v.id=e.checklist_version_id JOIN checklists c ON c.id=v.checklist_id
           WHERE e.group_id=? AND e.conversation_id=? ORDER BY e.id DESC""",
        (group_id, conversation_id),
    )


@router.get("/{eid}")
def api_get(eid: int):
    return get_evaluation(eid)


@router.post("/{eid}/stability")
def api_stability(eid: int, runs: int = 1):
    """Проверка стабильности: повторный прогон LLM (1–3 раза) и сравнение ответов по критериям."""
    try:
        return check_stability(eid, runs)
    except EvaluationFailed as e:
        raise HTTPException(502, str(e)) from e
