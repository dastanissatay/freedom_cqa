"""Выгрузка results.json в формате организаторов (CQA Student Guide Appendix, JSON 1.0).

    python scripts/export_results.py --group FBroker --out results/prod/results.json
    python scripts/export_results.py --group DEV --out results/dev/results.json

То же самое доступно в интерфейсе («Пакетная оценка» → «results.json») и по API: GET /api/export/{group}.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import db  # noqa: E402
from app.checklists import load_config  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.export import build_results, validate_results  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--group", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--team", default=None, help="по умолчанию TEAM_NAME из .env")
    p.add_argument("--model-name", default=None, help="по умолчанию — модель из оценок")
    p.add_argument("--mode", default=None, choices=["local", "external_api", "hybrid"])
    p.add_argument("--version-id", type=int, default=None, help="версия чек-листа (по умолчанию — последняя оценка)")
    p.add_argument("--with-appeals", action="store_true", help="учесть решения проверяющего")
    args = p.parse_args()

    db.init(get_settings().db_file)
    doc = build_results(args.group, args.version_id, args.team, args.mode, args.model_name, args.with_appeals)
    ok = [r for r in doc["results"] if r["status"] == "ok"]
    errs = []
    if ok:
        ev = db.query_one("SELECT checklist_version_id FROM evaluations WHERE group_id=? AND conversation_id=? ORDER BY id DESC LIMIT 1",
                          (args.group, ok[0]["conversation_id"]))
        errs = validate_results(doc, load_config(args.version_id or ev["checklist_version_id"]))

    out = Path(args.out)
    out = out if out.is_absolute() else ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{out}: {len(doc['results'])} записей, ok={len(ok)}, error={len(doc['results']) - len(ok)}, модель: {doc['model']['name']}")
    for r in doc["results"]:
        if r["status"] != "ok":
            print("  ERROR", r["conversation_id"], r["error"])
    if errs:
        print("ПРОВЕРКА НЕ ПРОЙДЕНА:", *errs, sep="\n  ")
        sys.exit(1)
    print("Проверка формата пройдена.")


if __name__ == "__main__":
    main()
