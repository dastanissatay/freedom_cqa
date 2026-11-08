"""Чек-листы с версиями. Любое сохранение создаёт новую неизменяемую версию."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import db
from .schemas import ChecklistConfig

SEED = Path(__file__).resolve().parents[1] / "seed" / "default_checklist.json"

router = APIRouter(prefix="/api/checklists", tags=["checklists"])


# ---------- сервис ----------

def create_checklist(config: ChecklistConfig, comment: str = "") -> dict:
    cid = db.execute("INSERT INTO checklists(name, created_at) VALUES (?, ?)", (config.name, db.now()))
    return add_version(cid, config, comment or "Создан")


def add_version(checklist_id: int, config: ChecklistConfig, comment: str = "") -> dict:
    if not db.query_one("SELECT id FROM checklists WHERE id=?", (checklist_id,)):
        raise HTTPException(404, "Чек-лист не найден")
    row = db.query_one("SELECT MAX(version) AS v FROM checklist_versions WHERE checklist_id=?", (checklist_id,))
    version = (row["v"] or 0) + 1
    vid = db.execute(
        "INSERT INTO checklist_versions(checklist_id, version, config_json, comment, created_at) VALUES (?,?,?,?,?)",
        (checklist_id, version, db.dumps(config.model_dump()), comment, db.now()),
    )
    db.execute("UPDATE checklists SET name=? WHERE id=?", (config.name, checklist_id))
    return get_version(vid)


def get_version(version_id: int) -> dict:
    row = db.query_one(
        "SELECT v.*, c.name AS checklist_name FROM checklist_versions v JOIN checklists c ON c.id=v.checklist_id WHERE v.id=?",
        (version_id,),
    )
    if not row:
        raise HTTPException(404, "Версия чек-листа не найдена")
    cfg = json.loads(row.pop("config_json"))
    row["config"] = cfg
    row["total_weight"] = ChecklistConfig.model_validate(cfg).total_weight
    return row


def load_config(version_id: int) -> ChecklistConfig:
    return ChecklistConfig.model_validate(get_version(version_id)["config"])


def list_checklists() -> list[dict]:
    rows = db.query("SELECT * FROM checklists ORDER BY id")
    for r in rows:
        r["versions"] = db.query(
            "SELECT id, version, comment, created_at FROM checklist_versions WHERE checklist_id=? ORDER BY version DESC",
            (r["id"],),
        )
        r["latest_version_id"] = r["versions"][0]["id"] if r["versions"] else None
    return rows


def ensure_seed() -> None:
    if db.query_one("SELECT id FROM checklists LIMIT 1"):
        return
    cfg = ChecklistConfig.model_validate(json.loads(SEED.read_text(encoding="utf-8")))
    create_checklist(cfg, "Начальная конфигурация из ТЗ")


# ---------- API ----------

class SaveBody(BaseModel):
    config: ChecklistConfig
    comment: str = ""


@router.get("")
def api_list():
    return list_checklists()


@router.post("")
def api_create(body: SaveBody):
    """Сохранить как НОВЫЙ чек-лист."""
    return create_checklist(body.config, body.comment)


@router.get("/versions/{version_id}")
def api_get_version(version_id: int):
    return get_version(version_id)


@router.post("/{checklist_id}/versions")
def api_add_version(checklist_id: int, body: SaveBody):
    """Сохранить изменения как новую версию существующего чек-листа."""
    return add_version(checklist_id, body.config, body.comment)


@router.post("/validate")
def api_validate(body: SaveBody):
    return {"ok": True, "total_weight": body.config.total_weight}
