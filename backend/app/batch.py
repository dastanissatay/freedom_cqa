"""Пакетная оценка всего набора с прогрессом + экспорт результатов."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from . import db
from .appeals import final_result_for
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


def export_rows(group_id: str, checklist_version_id: int | None) -> tuple[list[str], list[dict]]:
    sql = "SELECT id, conversation_id, checklist_version_id FROM evaluations WHERE group_id=?"
    params: list = [group_id]
    if checklist_version_id:
        sql += " AND checklist_version_id=?"
        params.append(checklist_version_id)
    latest: dict[str, dict] = {}
    for r in db.query(sql + " ORDER BY id", params):
        latest[r["conversation_id"]] = r
    crit_ids: list[str] = []
    rows = []
    for cid, r in sorted(latest.items()):
        fr = final_result_for(r["id"])
        cfg = load_config(r["checklist_version_id"])
        for c in cfg.criteria:
            if c.id not in crit_ids:
                crit_ids.append(c.id)
        row = {"conversation_id": cid, "evaluation_id": r["id"], "checklist_version_id": r["checklist_version_id"],
               "total": fr["final"]["total"], "original_total": fr["original"]["total"],
               "critical_flag": fr["final"]["critical_flag"]}
        for c in fr["final"]["criteria"]:
            row[f"c{c['criterion_id']}"] = c["answer"]
        rows.append(row)
    cols = ["conversation_id", "evaluation_id", "checklist_version_id", "total", "original_total", "critical_flag"]
    return cols + [f"c{c}" for c in crit_ids], rows


@router.get("/api/export/{group_id}")
def api_export(group_id: str, checklist_version_id: int | None = None, fmt: str = "csv"):
    cols, rows = export_rows(group_id, checklist_version_id)
    if fmt == "json":
        full = []
        for r in rows:
            from .evaluator import get_evaluation

            full.append(get_evaluation(r["evaluation_id"]))
        return Response(json.dumps(full, ensure_ascii=False, indent=1), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="cqa_{group_id}.json"'})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="cqa_{group_id}.csv"'})
