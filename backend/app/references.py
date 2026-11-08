"""Эталонная (ручная) разметка и отчёт о согласии LLM с эталоном.

Эталон привязан к версии чек-листа: ответы человека по каждому критерию.
Сравнивается исходный ответ LLM (до апелляций) из последней оценки этой версией.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from . import db, scoring
from .checklists import load_config
from .schemas import Answer, ChecklistConfig, Verdict

router = APIRouter(prefix="/api/references", tags=["references"])
quality_router = APIRouter(tags=["quality"])


class ReferenceBody(BaseModel):
    checklist_version_id: int
    answers: dict[str, Answer]
    notes: dict[str, str] = Field(default_factory=dict)
    author: str = ""

    @field_validator("answers", mode="before")
    @classmethod
    def _norm(cls, v):
        return {k: Verdict._norm_answer(a) for k, a in (v or {}).items()}


class ImportItem(BaseModel):
    conversation_id: str
    answers: dict[str, Answer]
    notes: dict[str, str] = Field(default_factory=dict)

    @field_validator("answers", mode="before")
    @classmethod
    def _norm(cls, v):
        return {k: Verdict._norm_answer(a) for k, a in (v or {}).items()}


class ImportBody(BaseModel):
    group_id: str
    checklist_version_id: int
    items: list[ImportItem]
    author: str = "import"


# ---------- сервис ----------

def _validate(cfg: ChecklistConfig, answers: dict[str, Any]) -> dict[str, Any]:
    unknown = [k for k in answers if cfg.criterion(k) is None]
    if unknown:
        raise HTTPException(400, f"Неизвестные критерии: {', '.join(unknown)}")
    missing = [c.id for c in cfg.criteria if c.id not in answers]
    if missing:
        raise HTTPException(400, f"Не размечены критерии: {', '.join(missing)}")
    for c in cfg.criteria:
        try:
            scoring.check_answer(c, answers[c.id])
        except scoring.ScoringError as e:
            raise HTTPException(400, str(e)) from e
    return {c.id: answers[c.id] for c in cfg.criteria}


def save_reference(group_id: str, conversation_id: str, version_id: int, answers: dict, notes: dict,
                   author: str = "", source: str = "manual") -> dict:
    cfg = load_config(version_id)
    clean = _validate(cfg, answers)
    notes = {k: v.strip() for k, v in (notes or {}).items() if v and v.strip() and cfg.criterion(k)}
    db.execute(
        """INSERT INTO reference_labels(group_id, conversation_id, checklist_version_id, answers_json, notes_json, author, source, updated_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(group_id, conversation_id, checklist_version_id) DO UPDATE SET
             answers_json=excluded.answers_json, notes_json=excluded.notes_json, author=excluded.author,
             source=excluded.source, updated_at=excluded.updated_at""",
        (group_id, conversation_id, version_id, db.dumps(clean), db.dumps(notes), author, source, db.now()),
    )
    return get_reference(group_id, conversation_id, version_id)


def _row(r: dict) -> dict:
    r["answers"] = json.loads(r.pop("answers_json"))
    r["notes"] = json.loads(r.pop("notes_json") or "{}")
    cfg = load_config(r["checklist_version_id"])
    res = scoring.compute(cfg, r["answers"])
    r["total"] = res["total"]
    r["critical_flag"] = res["critical_flag"]
    return r


def get_reference(group_id: str, conversation_id: str, version_id: int) -> Optional[dict]:
    r = db.query_one(
        "SELECT * FROM reference_labels WHERE group_id=? AND conversation_id=? AND checklist_version_id=?",
        (group_id, conversation_id, version_id),
    )
    return _row(r) if r else None


def _kappa(pairs: list[tuple[str, str]]) -> Optional[float]:
    """Каппа Коэна для двух «оценщиков» (LLM и эталон) по категориальным ответам."""
    n = len(pairs)
    if n == 0:
        return None
    p_o = sum(a == b for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    p_e = sum(ca[k] * cb.get(k, 0) for k in ca) / (n * n)
    if p_e >= 1:
        return 1.0 if p_o == 1 else None
    return round((p_o - p_e) / (1 - p_e), 3)


def quality_report(group_id: str, version_id: int) -> dict:
    cfg = load_config(version_id)
    refs = {r["conversation_id"]: _row(r) for r in db.query(
        "SELECT * FROM reference_labels WHERE group_id=? AND checklist_version_id=?", (group_id, version_id))}
    evals = {}
    for e in db.query(
        """SELECT id, conversation_id, verdicts_json, result_json FROM evaluations
           WHERE group_id=? AND checklist_version_id=? ORDER BY id""", (group_id, version_id)):
        evals[e["conversation_id"]] = e  # последняя оценка побеждает

    per_crit: dict[str, dict] = {c.id: {"pairs": [], "scale_diffs": [], "confusion": Counter()} for c in cfg.criteria}
    disagreements = []
    dialogs = []
    total_diffs = []
    flag = Counter()
    for cid, ref in sorted(refs.items()):
        ev = evals.get(cid)
        row = {"conversation_id": cid, "reference_total": ref["total"], "reference_flag": ref["critical_flag"],
               "evaluation_id": None, "llm_total": None, "llm_flag": None, "diff": None, "agree": None, "criteria": 0}
        if not ev:
            dialogs.append(row)
            continue
        verdicts = {v["criterion_id"]: v for v in json.loads(ev["verdicts_json"])}
        llm_res = json.loads(ev["result_json"])
        row.update(evaluation_id=ev["id"], llm_total=llm_res["total"], llm_flag=llm_res["critical_flag"])
        agree_n = 0
        for c in cfg.criteria:
            if c.id not in verdicts:
                continue
            la, ra = verdicts[c.id]["answer"], ref["answers"][c.id]
            pc = per_crit[c.id]
            pc["pairs"].append((str(la), str(ra)))
            if la == ra:
                agree_n += 1
            else:
                pc["confusion"][f"{la}→{ra}"] += 1
                disagreements.append({
                    "conversation_id": cid, "evaluation_id": ev["id"], "criterion_id": c.id, "criterion_title": c.title,
                    "llm": la, "reference": ra, "llm_explanation": verdicts[c.id].get("explanation", ""),
                    "note": ref["notes"].get(c.id, ""),
                })
            if c.type == "scale" and isinstance(la, int) and isinstance(ra, int):
                pc["scale_diffs"].append(abs(la - ra) / c.scale_max * 100)
            if c.type == "flag":
                key = ("tp" if la == "yes" and ra == "yes" else "fp" if la == "yes" else "fn" if ra == "yes" else "tn")
                flag[key] += 1
        row["criteria"] = len([c for c in cfg.criteria if c.id in verdicts])
        row["agree"] = agree_n
        if row["llm_total"] is not None and row["reference_total"] is not None:
            row["diff"] = round(row["llm_total"] - row["reference_total"], 2)
            total_diffs.append(abs(row["diff"]))
        dialogs.append(row)

    criteria_rows = []
    all_pairs = []
    for c in cfg.criteria:
        pc = per_crit[c.id]
        pairs = pc["pairs"]
        all_pairs += pairs
        n = len(pairs)
        agree = sum(a == b for a, b in pairs)
        criteria_rows.append({
            "criterion_id": c.id, "title": c.title, "type": c.type, "n": n, "agree": agree,
            "agreement_pct": round(agree / n * 100, 1) if n else None,
            "kappa": _kappa(pairs),
            "na_mismatch": sum((a == "na") != (b == "na") for a, b in pairs),
            "scale_mae_pct": round(sum(pc["scale_diffs"]) / len(pc["scale_diffs"]), 1) if pc["scale_diffs"] else None,
            "confusion": dict(pc["confusion"].most_common()),
        })
    compared = [d for d in dialogs if d["evaluation_id"]]
    n_pairs = len(all_pairs)
    return {
        "group_id": group_id,
        "checklist_version_id": version_id,
        "references": len(refs),
        "compared": len(compared),
        "missing_evaluation": [d["conversation_id"] for d in dialogs if not d["evaluation_id"]],
        "summary": {
            "criteria_agreement_pct": round(sum(a == b for a, b in all_pairs) / n_pairs * 100, 1) if n_pairs else None,
            "total_mae": round(sum(total_diffs) / len(total_diffs), 2) if total_diffs else None,
            "total_within_5_pct": round(sum(d <= 5 for d in total_diffs) / len(total_diffs) * 100, 1) if total_diffs else None,
            "total_within_10_pct": round(sum(d <= 10 for d in total_diffs) / len(total_diffs) * 100, 1) if total_diffs else None,
            "critical_flag": dict(flag),
        },
        "criteria": criteria_rows,
        "dialogs": dialogs,
        "disagreements": disagreements,
    }


# ---------- API ----------

@router.get("")
def api_list(group_id: str, checklist_version_id: Optional[int] = None):
    sql = "SELECT * FROM reference_labels WHERE group_id=?"
    params: list = [group_id]
    if checklist_version_id:
        sql += " AND checklist_version_id=?"
        params.append(checklist_version_id)
    return [_row(r) for r in db.query(sql + " ORDER BY conversation_id", params)]


@router.get("/{group_id}/{conversation_id}")
def api_get(group_id: str, conversation_id: str, checklist_version_id: int):
    return get_reference(group_id, conversation_id, checklist_version_id)


@router.put("/{group_id}/{conversation_id}")
def api_put(group_id: str, conversation_id: str, body: ReferenceBody):
    if not db.query_one("SELECT 1 FROM conversations WHERE group_id=? AND conversation_id=?", (group_id, conversation_id)):
        raise HTTPException(404, "Диалог не найден")
    return save_reference(group_id, conversation_id, body.checklist_version_id, body.answers, body.notes, body.author)


@router.delete("/{group_id}/{conversation_id}")
def api_delete(group_id: str, conversation_id: str, checklist_version_id: int):
    db.execute("DELETE FROM reference_labels WHERE group_id=? AND conversation_id=? AND checklist_version_id=?",
               (group_id, conversation_id, checklist_version_id))
    return {"ok": True}


@router.post("/import")
def api_import(body: ImportBody):
    """Импорт эталона из JSON: {group_id, checklist_version_id, items: [{conversation_id, answers: {"01": "yes", ...}}]}."""
    saved, errors = 0, {}
    for it in body.items:
        try:
            save_reference(body.group_id, it.conversation_id, body.checklist_version_id, it.answers, it.notes,
                           body.author, source="import")
            saved += 1
        except HTTPException as e:
            errors[it.conversation_id] = e.detail
    return {"saved": saved, "errors": errors}


@quality_router.get("/api/quality")
def api_quality(group_id: str, checklist_version_id: int):
    return quality_report(group_id, checklist_version_id)
