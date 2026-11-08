"""SQLite: схема и простые хелперы (без ORM)."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS checklists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checklist_versions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    checklist_id  INTEGER NOT NULL REFERENCES checklists(id),
    version       INTEGER NOT NULL,
    config_json   TEXT NOT NULL,
    comment       TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    UNIQUE (checklist_id, version)
);
CREATE TABLE IF NOT EXISTS conversations (
    group_id         TEXT NOT NULL,
    conversation_id  TEXT NOT NULL,
    channel          TEXT,
    language         TEXT,
    audio_url        TEXT,
    transcript_json  TEXT NOT NULL,
    fetched_at       TEXT NOT NULL,
    PRIMARY KEY (group_id, conversation_id)
);
CREATE TABLE IF NOT EXISTS evaluations (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id              TEXT NOT NULL,
    conversation_id       TEXT NOT NULL,
    checklist_version_id  INTEGER NOT NULL REFERENCES checklist_versions(id),
    provider              TEXT NOT NULL,
    model                 TEXT NOT NULL,
    verdicts_json         TEXT NOT NULL,
    result_json           TEXT NOT NULL,
    usage_json            TEXT NOT NULL DEFAULT '{}',
    cost_usd              REAL,
    attempts              INTEGER NOT NULL DEFAULT 1,
    created_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_eval_conv ON evaluations(group_id, conversation_id);
CREATE TABLE IF NOT EXISTS appeals (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id    INTEGER NOT NULL REFERENCES evaluations(id),
    criterion_id     TEXT NOT NULL,
    comment          TEXT NOT NULL,
    original_answer  TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'open',
    decision         TEXT,
    new_answer       TEXT,
    reviewer_reason  TEXT,
    created_at       TEXT NOT NULL,
    resolved_at      TEXT
);
CREATE INDEX IF NOT EXISTS ix_appeal_eval ON appeals(evaluation_id);
-- эталонная (ручная) разметка для проверки качества LLM
CREATE TABLE IF NOT EXISTS reference_labels (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id              TEXT NOT NULL,
    conversation_id       TEXT NOT NULL,
    checklist_version_id  INTEGER NOT NULL REFERENCES checklist_versions(id),
    answers_json          TEXT NOT NULL,
    notes_json            TEXT NOT NULL DEFAULT '{}',
    author                TEXT NOT NULL DEFAULT '',
    source                TEXT NOT NULL DEFAULT 'manual',
    updated_at            TEXT NOT NULL,
    UNIQUE (group_id, conversation_id, checklist_version_id)
);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_path: Path | None = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init(path: Path) -> None:
    global _conn, _path
    with _lock:
        if _conn is not None and _path == path:
            return
        if _conn is not None:
            _conn.close()
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        _conn.execute("PRAGMA journal_mode = WAL") if str(path) != ":memory:" else None
        _conn.executescript(SCHEMA)
        _conn.commit()
        _path = path


def conn() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("БД не инициализирована")
    return _conn


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    with _lock:
        cur = conn().execute(sql, tuple(params))
        conn().commit()
        return cur.lastrowid


def query(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, tuple(params)).fetchall()]


def query_one(sql: str, params: Iterable[Any] = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
