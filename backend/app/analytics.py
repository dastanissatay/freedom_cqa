"""Сводка по набору для руководителя контроля качества: типовые ошибки, разрезы, худшие диалоги.

Берётся последняя оценка каждого диалога указанной версией чек-листа, с учётом решений по апелляциям.
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from typing import Optional

from fastapi import APIRouter

from . import db
from .appeals import final_result_for
from .checklists import load_config

router = APIRouter(tags=["analytics"])

BUCKETS = [(0, 50, "0–50"), (50, 70, "50–70"), (70, 85, "70–85"), (85, 100.01, "85–100")]


def summary(group_id: str, version_id: int) -> dict:
    cfg = load_config(version_id)
    convs = {r["conversation_id"]: r for r in db.query(
        "SELECT conversation_id, channel, language FROM conversations WHERE group_id=?", (group_id,))}
    latest: dict[str, int] = {}
    for e in db.query("SELECT id, conversation_id FROM evaluations WHERE group_id=? AND checklist_version_id=? ORDER BY id",
                      (group_id, version_id)):
        latest[e["conversation_id"]] = e["id"]

    totals, dialogs = [], []
    by_lang: dict[str, list] = defaultdict(list)
    by_channel: dict[str, list] = defaultdict(list)
    crit_stats = {c.id: {"answers": Counter(), "applicable": 0, "fail": 0, "earned": 0.0, "weight": 0.0} for c in cfg.criteria}
    critical = []
    for cid, eid in sorted(latest.items()):
        fin = final_result_for(eid)["final"]
        meta = convs.get(cid, {})
        t = fin["total"]
        dialogs.append({"conversation_id": cid, "evaluation_id": eid, "total": t, "critical_flag": fin["critical_flag"],
                        "language": meta.get("language"), "channel": meta.get("channel")})
        if t is not None:
            totals.append(t)
            by_lang[meta.get("language") or "?"].append(t)
            by_channel[meta.get("channel") or "?"].append(t)
        if fin["critical_flag"]:
            critical.append({"conversation_id": cid, "evaluation_id": eid, "criteria": fin["critical_criteria"]})
        for row in fin["criteria"]:
            st = crit_stats.get(row["criterion_id"])
            if st is None:
                continue
            c = cfg.criterion(row["criterion_id"])
            st["answers"][str(row["answer"])] += 1
            if c.type == "flag":
                st["fail"] += row["answer"] == "yes"
                continue
            if not row["applicable"]:
                continue
            st["applicable"] += 1
            st["earned"] += row["points"] or 0
            st["weight"] += row["weight"]
            st["fail"] += row["answer"] == "no" or (c.type == "scale" and row["answer"] < c.scale_max)

    n = len(dialogs)
    criteria = []
    for c in cfg.criteria:
        st = crit_stats[c.id]
        base = n if c.type == "flag" else st["applicable"]
        criteria.append({
            "criterion_id": c.id, "title": c.title, "type": c.type, "weight": c.weight,
            "evaluated": n, "applicable": base, "na": st["answers"].get("na", 0),
            "fail": st["fail"], "fail_pct": round(st["fail"] / base * 100, 1) if base else None,
            "score_pct": round(st["earned"] / st["weight"] * 100, 1) if st["weight"] else None,
            "answers": dict(st["answers"]),
        })

    def agg(d: dict[str, list]) -> list[dict]:
        return [{"key": k, "n": len(v), "avg": round(statistics.mean(v), 2)} for k, v in sorted(d.items())]

    return {
        "group_id": group_id,
        "checklist_version_id": version_id,
        "dialogs_total": len(convs),
        "evaluated": n,
        "avg_total": round(statistics.mean(totals), 2) if totals else None,
        "median_total": round(statistics.median(totals), 2) if totals else None,
        "distribution": [{"range": label, "n": sum(lo <= t < hi for t in totals)} for lo, hi, label in BUCKETS],
        "by_language": agg(by_lang),
        "by_channel": agg(by_channel),
        "criteria": criteria,
        "top_problems": [c for c in sorted(criteria, key=lambda c: -(c["fail_pct"] or 0)) if c["type"] != "flag" and c["fail"]][:3],
        "critical": critical,
        "worst": sorted([d for d in dialogs if d["total"] is not None], key=lambda d: d["total"])[:5],
    }


@router.get("/api/summary")
def api_summary(group_id: str, checklist_version_id: int):
    return summary(group_id, checklist_version_id)
