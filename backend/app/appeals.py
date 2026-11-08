"""Апелляции и решения проверяющего. Итог пересчитывается кодом, без повторного вызова LLM."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException

from . import db, scoring
from .checklists import load_config
from .schemas import AppealCreate, AppealDecision

router = APIRouter(prefix="/api/appeals", tags=["appeals"])


def _load(v: Any):
    return None if v is None else json.loads(v)


def _row(a: dict) -> dict:
    a["original_answer"] = _load(a["original_answer"])
    a["new_answer"] = _load(a["new_answer"])
    return a


def appeals_for(eid: int) -> list[dict]:
    return [_row(a) for a in db.query("SELECT * FROM appeals WHERE evaluation_id=? ORDER BY id", (eid,))]


def final_result_for(eid: int) -> dict:
    """Исходный результат (LLM), применённые решения и финальный пересчитанный результат."""
    ev = db.query_one("SELECT checklist_version_id, verdicts_json, result_json FROM evaluations WHERE id=?", (eid,))
    if not ev:
        raise HTTPException(404, "Оценка не найдена")
    cfg = load_config(ev["checklist_version_id"])
    original = json.loads(ev["result_json"])
    answers = {v["criterion_id"]: v["answer"] for v in json.loads(ev["verdicts_json"])}
    appeals = appeals_for(eid)
    overrides: dict[str, Any] = {}
    for a in appeals:  # по порядку: последнее решение по критерию побеждает
        if a["status"] == "resolved" and a["decision"] == "change":
            overrides[a["criterion_id"]] = a["new_answer"]
    final = scoring.compute(cfg, {**answers, **overrides}) if overrides else original
    return {
        "original": original,
        "final": final,
        "overrides": overrides,
        "appeals": appeals,
        "open_appeals": sum(1 for a in appeals if a["status"] == "open"),
    }


def _effective_answer(eid: int, criterion_id: str):
    fr = final_result_for(eid)
    row = next((c for c in fr["final"]["criteria"] if c["criterion_id"] == criterion_id), None)
    if row is None:
        raise HTTPException(400, f"Критерия {criterion_id} нет в чек-листе этой оценки")
    return row["answer"]


# ---------- API ----------

@router.post("")
def api_create(body: AppealCreate):
    if not db.query_one("SELECT id FROM evaluations WHERE id=?", (body.evaluation_id,)):
        raise HTTPException(404, "Оценка не найдена")
    if db.query_one(
        "SELECT id FROM appeals WHERE evaluation_id=? AND criterion_id=? AND status='open'",
        (body.evaluation_id, body.criterion_id),
    ):
        raise HTTPException(409, "По этому критерию уже есть открытая апелляция")
    original = _effective_answer(body.evaluation_id, body.criterion_id)
    aid = db.execute(
        "INSERT INTO appeals(evaluation_id, criterion_id, comment, original_answer, status, created_at) VALUES (?,?,?,?, 'open', ?)",
        (body.evaluation_id, body.criterion_id, body.comment.strip(), json.dumps(original), db.now()),
    )
    return _row(db.query_one("SELECT * FROM appeals WHERE id=?", (aid,)))


@router.get("")
def api_list(status: str | None = None, group_id: str | None = None):
    sql = """SELECT a.*, e.group_id, e.conversation_id, e.checklist_version_id
             FROM appeals a JOIN evaluations e ON e.id=a.evaluation_id WHERE 1=1"""
    params: list = []
    if status:
        sql += " AND a.status=?"
        params.append(status)
    if group_id:
        sql += " AND e.group_id=?"
        params.append(group_id)
    sql += " ORDER BY a.status='open' DESC, a.id DESC"
    return [_row(a) for a in db.query(sql, params)]


@router.post("/{appeal_id}/decision")
def api_decide(appeal_id: int, body: AppealDecision):
    a = db.query_one("SELECT * FROM appeals WHERE id=?", (appeal_id,))
    if not a:
        raise HTTPException(404, "Апелляция не найдена")
    if a["status"] != "open":
        raise HTTPException(409, "Апелляция уже рассмотрена")
    ev = db.query_one("SELECT checklist_version_id FROM evaluations WHERE id=?", (a["evaluation_id"],))
    cfg = load_config(ev["checklist_version_id"])
    crit = cfg.criterion(a["criterion_id"])
    original = json.loads(a["original_answer"])
    if body.decision == "change":
        if body.new_answer is None:
            raise HTTPException(400, "Укажите новое значение")
        try:
            scoring.check_answer(crit, body.new_answer)
        except scoring.ScoringError as e:
            raise HTTPException(400, str(e)) from e
        new_answer = body.new_answer
    else:
        new_answer = original
    db.execute(
        """UPDATE appeals SET status='resolved', decision=?, new_answer=?, reviewer_reason=?, resolved_at=? WHERE id=?""",
        (body.decision, json.dumps(new_answer), body.reason.strip(), db.now(), appeal_id),
    )
    return {"appeal": _row(db.query_one("SELECT * FROM appeals WHERE id=?", (appeal_id,))),
            **final_result_for(a["evaluation_id"])}
