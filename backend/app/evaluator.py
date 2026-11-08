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


def evaluate(group_id: str, conversation_id: str, checklist_version_id: int) -> dict:
    conv = get_conversation(group_id, conversation_id)
    cfg = load_config(checklist_version_id)
    adapter = get_adapter()
    settings = get_settings()

    system = build_system(cfg)
    schema = build_schema(cfg)
    n = len(conv["transcript"])
    if n == 0:
        raise EvaluationFailed("Пустой транскрипт")

    usage_total: dict[str, int] = {}
    errors: list[str] = []
    warnings: list[str] = []
    verdicts = None
    model = adapter.model
    attempts = 0
    started = time.time()
    for attempt in range(settings.llm_max_retries + 1):
        attempts = attempt + 1
        user = build_user(conv, errors or None)
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
            usage_total[k] = usage_total.get(k, 0) + int(v or 0)
        parsed, errors, warnings = parse_and_validate(resp.text, cfg, n)
        if not errors:
            verdicts = parsed
            break
    if verdicts is None:
        raise EvaluationFailed("Модель не вернула корректный ответ: " + "; ".join(errors[:5]))

    answers = {v.criterion_id: v.answer for v in verdicts}
    result = scoring.compute(cfg, answers)
    cost = cost_usd(adapter.model if adapter.provider == "mock" else model, usage_total)
    if cost is None:
        cost = cost_usd(adapter.model, usage_total)
    usage_total["latency_sec"] = round(time.time() - started, 2)
    usage_total["warnings"] = warnings  # type: ignore[assignment]

    eid = db.execute(
        """INSERT INTO evaluations(group_id, conversation_id, checklist_version_id, provider, model,
             verdicts_json, result_json, usage_json, cost_usd, attempts, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (group_id, conversation_id, checklist_version_id, adapter.provider, model,
         db.dumps([v.model_dump() for v in verdicts]), db.dumps(result), db.dumps(usage_total), cost, attempts, db.now()),
    )
    return get_evaluation(eid)


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
