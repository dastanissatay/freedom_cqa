"""Пакетная оценка всего набора с прогрессом."""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, HTTPException

from . import db
from .checklists import load_config
from .config import get_settings
from .evaluator import EvaluationFailed, evaluate
from .schemas import BatchRequest

router = APIRouter(tags=["batch"])

JOBS: dict[str, dict] = {}
_TASKS: dict[str, asyncio.Task] = {}


async def _run(job: dict, ids: list[str]) -> None:
    sem = asyncio.Semaphore(max(1, get_settings().batch_concurrency))

    async def one(cid: str):
        async with sem:
            if job["status"] == "cancelled":
                return
            job["in_progress"].append(cid)
            try:
                ev = await asyncio.to_thread(evaluate, job["group_id"], cid, job["checklist_version_id"])
                job["done"] += 1
                job["cost_usd"] += ev.get("cost_usd") or 0
                job["results"][cid] = {"evaluation_id": ev["id"], "total": ev["final"]["total"],
                                       "critical_flag": ev["final"]["critical_flag"]}
            except (EvaluationFailed, HTTPException, Exception) as e:  # noqa: BLE001 — фиксируем любую ошибку диалога
                job["failed"] += 1
                job["errors"][cid] = getattr(e, "detail", None) or str(e)
            finally:
                job["in_progress"].remove(cid)

    await asyncio.gather(*(one(c) for c in ids))
    if job["status"] != "cancelled":
        job["status"] = "done"
    job["finished_at"] = db.now()


@router.post("/api/batch")
async def api_start(body: BatchRequest):
    load_config(body.checklist_version_id)  # 404, если версии нет
    rows = db.query("SELECT conversation_id FROM conversations WHERE group_id=? ORDER BY conversation_id", (body.group_id,))
    ids = [r["conversation_id"] for r in rows]
    if body.conversation_ids:
        ids = [i for i in ids if i in set(body.conversation_ids)]
    if body.only_missing:
        done = {r["conversation_id"] for r in db.query(
            "SELECT DISTINCT conversation_id FROM evaluations WHERE group_id=? AND checklist_version_id=?",
            (body.group_id, body.checklist_version_id))}
        ids = [i for i in ids if i not in done]
    if not ids:
        raise HTTPException(400, "Нет диалогов для оценки (загрузите набор)")
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "group_id": body.group_id, "checklist_version_id": body.checklist_version_id,
           "total": len(ids), "done": 0, "failed": 0, "status": "running", "in_progress": [],
           "errors": {}, "results": {}, "cost_usd": 0.0, "started_at": db.now(), "finished_at": None}
    JOBS[jid] = job
    _TASKS[jid] = asyncio.create_task(_run(job, ids))
    return job


@router.get("/api/batch/{jid}")
def api_status(jid: str):
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404, "Задача не найдена")
    return job


@router.post("/api/batch/{jid}/cancel")
def api_cancel(jid: str):
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404, "Задача не найдена")
    if job["status"] == "running":
        job["status"] = "cancelled"
    return job


@router.get("/api/batch")
def api_jobs():
    return sorted(JOBS.values(), key=lambda j: j["started_at"], reverse=True)
