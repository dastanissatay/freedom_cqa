"""Выгрузка результатов: results.json в формате организаторов (JSON 1.0) и CSV.

Коды критериев берутся из конфигурации чек-листа (поле export_code), поэтому заменённый
или новый критерий выгружается без правки кода.
"""
from __future__ import annotations

import csv
import io
import json
import re
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from . import db, scoring
from .checklists import load_config
from .config import get_settings
from .schemas import ChecklistConfig, Criterion

router = APIRouter(tags=["export"])

# коды для чек-листов, сохранённых до появления поля export_code
LEGACY_CODES = {"01": "contact", "02": "discovery", "03": "identification", "04": "consultation",
                "05": "empathy", "06": "dialogue", "07": "closure", "08": "critical"}

_TURN_REF = re.compile(r"\[(\d+)\]")


def export_code(c: Criterion) -> str:
    return c.export_code or LEGACY_CODES.get(c.id) or c.id


def renumber_refs(text: str) -> str:
    """Ссылки [9] в объяснениях LLM даны с 0 — переводим в нумерацию с 1, как evidence_turns."""
    return _TURN_REF.sub(lambda m: f"[{int(m.group(1)) + 1}]", text)


def to_value(answer: Any) -> Any:
    return {"yes": True, "no": False, "na": None}.get(answer, answer) if isinstance(answer, str) else int(answer)


def allowed_values(c: Criterion) -> list:
    vals: list = [lvl.value for lvl in c.scale] if c.type == "scale" else [True, False]
    if c.na_allowed:
        vals.append(None)
    return vals


def _latest_evaluation(group: str, cid: str, version_id: Optional[int]) -> Optional[dict]:
    sql = "SELECT * FROM evaluations WHERE group_id=? AND conversation_id=?"
    params: list = [group, cid]
    if version_id:
        sql += " AND checklist_version_id=?"
        params.append(version_id)
    return db.query_one(sql + " ORDER BY id DESC LIMIT 1", params)


def export_conversation(group: str, cid: str, version_id: Optional[int], with_appeals: bool) -> dict:
    ev = _latest_evaluation(group, cid, version_id)
    if not ev:
        return {"conversation_id": cid, "status": "error", "criteria": [], "score": None,
                "error": "Диалог не был оценён системой."}
    try:
        cfg = load_config(ev["checklist_version_id"])
        verdicts = {v["criterion_id"]: v for v in json.loads(ev["verdicts_json"])}
        answers = {k: v["answer"] for k, v in verdicts.items()}
        if with_appeals:
            for a in db.query("SELECT criterion_id, new_answer FROM appeals WHERE evaluation_id=? "
                              "AND status='resolved' AND decision='change' ORDER BY id", (ev["id"],)):
                answers[a["criterion_id"]] = json.loads(a["new_answer"])
        result = scoring.compute(cfg, answers)
        n = len(json.loads(db.query_one("SELECT transcript_json FROM conversations WHERE group_id=? AND conversation_id=?",
                                        (group, cid))["transcript_json"]))
        criteria = []
        for c in cfg.criteria:
            v = verdicts[c.id]
            turns = v.get("evidence") or []
            if not turns and v.get("checked_range"):  # пропущенное действие — участок, где ожидалось
                a, b = v["checked_range"]
                turns = list(range(a, b + 1))
            criteria.append({
                "criterion_id": export_code(c),
                "value": to_value(answers[c.id]),
                "rationale": renumber_refs(v.get("explanation", "").strip()),
                "evidence_turns": sorted({int(i) + 1 for i in turns if 0 <= int(i) < n}),  # нумерация с 1
            })
        return {"conversation_id": cid, "status": "ok", "criteria": criteria,
                "score": {"final_score": result["total"], "critical_flag": result["critical_flag"]}}
    except Exception as e:  # noqa: BLE001 — по формату ошибку не скрываем, а пишем в error
        return {"conversation_id": cid, "status": "error", "criteria": [], "score": None, "error": str(e)}


def model_mode() -> str:
    return {"anthropic": "external_api"}.get(get_settings().llm_provider, "local")


def build_results(group: str, version_id: Optional[int] = None, team: Optional[str] = None,
                  mode: Optional[str] = None, model_name: Optional[str] = None, with_appeals: bool = False) -> dict:
    ids = [r["conversation_id"] for r in db.query(
        "SELECT conversation_id FROM conversations WHERE group_id=? ORDER BY conversation_id", (group,))]
    if not ids:
        raise HTTPException(404, f"Набор {group} не загружен")
    models = sorted({r["model"] for r in db.query("SELECT DISTINCT model FROM evaluations WHERE group_id=?", (group,))})
    return {
        "schema_version": "1.0",
        "team_name": team or get_settings().team_name,
        "model": {"mode": mode or model_mode(), "name": model_name or ", ".join(models) or get_settings().llm_model},
        "results": [export_conversation(group, cid, version_id, with_appeals) for cid in ids],
    }


def validate_results(doc: dict, cfg: ChecklistConfig) -> list[str]:
    """Проверка по правилам формата: уникальные ID, все критерии по разу, допустимые значения."""
    errs = []
    ids = [r["conversation_id"] for r in doc["results"]]
    if len(ids) != len(set(ids)):
        errs.append("повторяющиеся conversation_id")
    allowed = {export_code(c): allowed_values(c) for c in cfg.criteria}
    flag_code = next((export_code(c) for c in cfg.criteria if c.type == "flag"), None)
    for r in doc["results"]:
        if r["status"] != "ok":
            continue
        codes = [c["criterion_id"] for c in r["criteria"]]
        if sorted(codes) != sorted(allowed):
            errs.append(f"{r['conversation_id']}: набор критериев не совпадает с чек-листом")
            continue
        for c in r["criteria"]:
            v = c["value"]
            if not any(v is a if a is None or isinstance(a, bool) else (type(v) is int and v == a) for a in allowed[c["criterion_id"]]):
                errs.append(f"{r['conversation_id']}: {c['criterion_id']}={v!r} недопустимо")
        if flag_code:
            flag = next(c["value"] for c in r["criteria"] if c["criterion_id"] == flag_code)
            if r["score"]["critical_flag"] != flag:
                errs.append(f"{r['conversation_id']}: critical_flag не совпадает с {flag_code}")
    return errs


def csv_export(group: str, version_id: Optional[int]) -> str:
    rows, crit_ids = [], []
    for r in build_results(group, version_id)["results"]:
        row = {"conversation_id": r["conversation_id"], "status": r["status"],
               "final_score": (r["score"] or {}).get("final_score"), "critical_flag": (r["score"] or {}).get("critical_flag")}
        for c in r["criteria"]:
            row[c["criterion_id"]] = c["value"]
            if c["criterion_id"] not in crit_ids:
                crit_ids.append(c["criterion_id"])
        rows.append(row)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["conversation_id", "status", "final_score", "critical_flag", *crit_ids], extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return "﻿" + buf.getvalue()


@router.get("/api/export/{group_id}")
def api_export(group_id: str, checklist_version_id: Optional[int] = None, fmt: str = "results",
               with_appeals: bool = False):
    """fmt=results — results.json в формате организаторов; fmt=csv — таблица для просмотра."""
    if fmt == "csv":
        return Response(csv_export(group_id, checklist_version_id), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="cqa_{group_id}.csv"'})
    doc = build_results(group_id, checklist_version_id, with_appeals=with_appeals)
    return Response(json.dumps(doc, ensure_ascii=False, indent=2), media_type="application/json; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="results.json"'})
