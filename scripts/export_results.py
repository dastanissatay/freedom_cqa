"""Выгрузка результатов в формат организаторов (CQA Student Guide Appendix, JSON 1.0).

Пример:
    python scripts/export_results.py --group FBroker --out results/prod/results.json --team "Команда"
    python scripts/export_results.py --group DEV --out results/dev/results.json --team "Команда"

Берётся последняя оценка каждого диалога набора (по умолчанию — исходный ответ LLM;
с флагом --with-appeals — с учётом решений проверяющего). Диалог без оценки или с ошибкой
выгружается со status="error", как требует гайд.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import db, scoring  # noqa: E402
from app.checklists import load_config  # noqa: E402
from app.config import get_settings  # noqa: E402

# id критерия в нашем чек-листе → код критерия в формате организаторов
CODES = {
    "01": "contact",
    "02": "discovery",
    "03": "identification",
    "04": "consultation",
    "05": "empathy",
    "06": "dialogue",
    "07": "closure",
    "08": "critical",
}


_TURN_REF = re.compile(r"\[(\d+)\]")


def renumber_refs(text: str) -> str:
    """В объяснениях LLM ссылки вида [9] даны по нумерации с 0 — переводим в нумерацию с 1, как evidence_turns."""
    return _TURN_REF.sub(lambda m: f"[{int(m.group(1)) + 1}]", text)


def to_value(answer):
    if answer == "yes":
        return True
    if answer == "no":
        return False
    if answer == "na":
        return None
    return int(answer)


def export_conversation(group: str, cid: str, version_id: int | None, with_appeals: bool) -> dict:
    sql = "SELECT * FROM evaluations WHERE group_id=? AND conversation_id=?"
    params: list = [group, cid]
    if version_id:
        sql += " AND checklist_version_id=?"
        params.append(version_id)
    ev = db.query_one(sql + " ORDER BY id DESC LIMIT 1", params)
    if not ev:
        return {"conversation_id": cid, "status": "error", "criteria": [], "score": None,
                "error": "Диалог не был оценён системой."}
    try:
        cfg = load_config(ev["checklist_version_id"])
        verdicts = {v["criterion_id"]: v for v in json.loads(ev["verdicts_json"])}
        answers = {k: v["answer"] for k, v in verdicts.items()}
        if with_appeals:
            for a in db.query(
                "SELECT criterion_id, new_answer FROM appeals WHERE evaluation_id=? AND status='resolved' AND decision='change' ORDER BY id",
                (ev["id"],),
            ):
                answers[a["criterion_id"]] = json.loads(a["new_answer"])
        missing = [k for k in CODES if k not in answers]
        if missing:
            raise ValueError(f"в оценке нет критериев {missing} (чек-лист не совпадает с основным)")
        result = scoring.compute(cfg, answers)
        n = len(json.loads(db.query_one(
            "SELECT transcript_json FROM conversations WHERE group_id=? AND conversation_id=?", (group, cid))["transcript_json"]))
        criteria = []
        for our_id, code in CODES.items():
            v = verdicts[our_id]
            turns = v.get("evidence") or []
            if not turns and v.get("checked_range"):  # пропущенное действие: участок, где ожидалось
                a, b = v["checked_range"]
                turns = list(range(a, b + 1))
            criteria.append({
                "criterion_id": code,
                "value": to_value(answers[our_id]),
                "rationale": renumber_refs(v.get("explanation", "").strip()),
                # у нас реплики нумеруются с 0, в формате организаторов — с 1
                "evidence_turns": sorted({int(i) + 1 for i in turns if 0 <= int(i) < n}),
            })
        return {
            "conversation_id": cid,
            "status": "ok",
            "criteria": criteria,
            "score": {"final_score": result["total"], "critical_flag": criteria[-1]["value"] is True},
        }
    except Exception as e:  # noqa: BLE001 — по гайду ошибку не скрываем, а пишем в error
        return {"conversation_id": cid, "status": "error", "criteria": [], "score": None, "error": str(e)}


def validate(doc: dict, expected_ids: list[str]) -> list[str]:
    errs = []
    ids = [r["conversation_id"] for r in doc["results"]]
    if len(ids) != len(set(ids)):
        errs.append("повторяющиеся conversation_id")
    if set(ids) != set(expected_ids):
        errs.append("набор ID не совпадает с API")
    allowed = {"contact": {True, False}, "discovery": {True, False, None}, "identification": {True, False, None},
               "consultation": {0, 5, 10, 15, 20, None}, "empathy": {True, False}, "dialogue": {0, 4, 7, 10},
               "closure": {True, False, None}, "critical": {True, False}}
    for r in doc["results"]:
        if r["status"] != "ok":
            continue
        codes = [c["criterion_id"] for c in r["criteria"]]
        if sorted(codes) != sorted(allowed):
            errs.append(f"{r['conversation_id']}: не все 8 критериев")
        for c in r["criteria"]:
            v = c["value"]
            ok = any(v is a if isinstance(a, bool) or a is None else (type(v) is int and v == a) for a in allowed[c["criterion_id"]])
            if not ok:
                errs.append(f"{r['conversation_id']}: {c['criterion_id']}={v!r} недопустимо")
        crit = next(c["value"] for c in r["criteria"] if c["criterion_id"] == "critical")
        if r["score"]["critical_flag"] != crit:
            errs.append(f"{r['conversation_id']}: critical_flag != critical")
    return errs


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--group", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--team", required=True)
    p.add_argument("--model-name", default=None, help="по умолчанию — модель из оценок")
    p.add_argument("--mode", default="external_api", choices=["local", "external_api", "hybrid"])
    p.add_argument("--version-id", type=int, default=None, help="версия чек-листа (по умолчанию — последняя оценка)")
    p.add_argument("--with-appeals", action="store_true", help="учесть решения проверяющего")
    args = p.parse_args()

    db.init(get_settings().db_file)
    ids = [r["conversation_id"] for r in db.query(
        "SELECT conversation_id FROM conversations WHERE group_id=? ORDER BY conversation_id", (args.group,))]
    if not ids:
        sys.exit(f"Набор {args.group} не загружен в БД")
    models = sorted({r["model"] for r in db.query(
        "SELECT DISTINCT model FROM evaluations WHERE group_id=?", (args.group,))})
    doc = {
        "schema_version": "1.0",
        "team_name": args.team,
        "model": {"mode": args.mode, "name": args.model_name or ", ".join(models) or get_settings().llm_model},
        "results": [export_conversation(args.group, cid, args.version_id, args.with_appeals) for cid in ids],
    }
    errs = validate(doc, ids)
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ok = sum(r["status"] == "ok" for r in doc["results"])
    print(f"{out}: {len(ids)} записей, ok={ok}, error={len(ids) - ok}, модель: {doc['model']['name']}")
    for r in doc["results"]:
        if r["status"] != "ok":
            print("  ERROR", r["conversation_id"], r["error"])
    if errs:
        print("ПРОВЕРКА НЕ ПРОЙДЕНА:", *errs, sep="\n  ")
        sys.exit(1)
    print("Проверка формата пройдена.")


if __name__ == "__main__":
    main()
