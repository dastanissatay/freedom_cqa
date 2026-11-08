"""Прокси к API организаторов + кэш диалогов + аудио-прокси.

Браузер не ходит к API напрямую (CORS): всё идёт через этот модуль.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import StreamingResponse

from . import db
from .config import get_settings

router = APIRouter(tags=["dataset"])

_MMSS = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,](\d+))?$")


# ---------- время ----------

def parse_ts(ts: Any) -> Optional[float]:
    """Возвращает абсолютное значение в секундах (MM:SS / HH:MM:SS) или epoch (ISO 8601)."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    s = str(ts).strip()
    m = _MMSS.match(s)
    if m:
        h, mm, ss, frac = m.groups()
        val = int(h or 0) * 3600 + int(mm) * 60 + int(ss)
        if frac:
            val += float("0." + frac)
        return float(val)
    try:
        iso = s.replace("Z", "+00:00")
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


def normalize_transcript(raw: Any) -> list[dict]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = []
    msgs = []
    for i, m in enumerate(raw or []):
        msgs.append(
            {
                "index": i,
                "timestamp": m.get("timestamp"),  # исходная метка сохраняется как есть
                "speaker": (m.get("speaker") or "").strip().lower(),
                "text": m.get("text") or "",
            }
        )
    first = next((parse_ts(m["timestamp"]) for m in msgs if parse_ts(m["timestamp"]) is not None), None)
    for m in msgs:
        t = parse_ts(m["timestamp"])
        # смещение = timestamp реплики − timestamp первой реплики
        m["offset_sec"] = None if (t is None or first is None) else round(max(0.0, t - first), 3)
    return msgs


# ---------- хранилище ----------

def store_records(group_id: str, records: list[dict]) -> int:
    now = db.now()
    n = 0
    for r in records:
        cid = r.get("conversation_id")
        if not cid:
            continue
        transcript = normalize_transcript(r.get("transcript"))
        db.execute(
            """INSERT INTO conversations(group_id, conversation_id, channel, language, audio_url, transcript_json, fetched_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(group_id, conversation_id) DO UPDATE SET
                 channel=excluded.channel, language=excluded.language, audio_url=excluded.audio_url,
                 transcript_json=excluded.transcript_json, fetched_at=excluded.fetched_at""",
            (group_id, cid, r.get("channel"), (r.get("language") or "").upper(), r.get("audio_url"),
             db.dumps(transcript), now),
        )
        n += 1
    return n


def get_conversation(group_id: str, conversation_id: str) -> dict:
    row = db.query_one(
        "SELECT * FROM conversations WHERE group_id=? AND conversation_id=?", (group_id, conversation_id)
    )
    if not row:
        raise HTTPException(404, f"Диалог {conversation_id} не найден в наборе {group_id}. Загрузите набор.")
    row["transcript"] = json.loads(row.pop("transcript_json"))
    return row


def extract_records(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        raise HTTPException(502, "Неожиданный формат ответа API")
    if payload.get("isSuccess") is False:
        raise HTTPException(502, f"API вернул ошибку: {payload.get('error') or 'isSuccess=false'}")
    data = payload.get("data", payload)
    if isinstance(data, list):
        return data
    recs = data.get("records")
    if not isinstance(recs, list):
        raise HTTPException(502, "В ответе API нет data.records")
    return recs


def build_dataset_url(url: str, group_id: str) -> str:
    """Подставляет group_id, сохраняя остальные параметры URL (api-version, sp, sv, sig у Power Automate).

    Важно: httpx с params= заменяет всю строку запроса, поэтому параметр ставим в сам URL.
    """
    return str(httpx.URL(url).copy_set_param("group_id", group_id))


async def fetch_remote(group_id: str) -> int:
    url = get_settings().dataset_api_url
    if not url:
        raise HTTPException(400, "DATASET_API_URL не задан в .env")
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(build_dataset_url(url, group_id))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Не удалось подключиться к API данных: {type(e).__name__}: {e}") from e
    if resp.status_code >= 400:
        # в текст ошибки не включаем URL: в нём подпись доступа (sig)
        raise HTTPException(502, f"API данных вернул {resp.status_code}: {resp.text[:300]}")
    try:
        payload = resp.json()
    except ValueError as e:
        raise HTTPException(502, f"API данных вернул не JSON: {resp.text[:200]}") from e
    return store_records(group_id, extract_records(payload))


def list_conversations(group_id: str) -> list[dict]:
    rows = db.query(
        """SELECT c.conversation_id, c.channel, c.language, c.audio_url, c.transcript_json, c.fetched_at
           FROM conversations c WHERE c.group_id=? ORDER BY c.conversation_id""",
        (group_id,),
    )
    out = []
    labeled: dict[str, list[int]] = {}
    for x in db.query("SELECT conversation_id, checklist_version_id FROM reference_labels WHERE group_id=?", (group_id,)):
        labeled.setdefault(x["conversation_id"], []).append(x["checklist_version_id"])
    for r in rows:
        r["reference_versions"] = labeled.get(r["conversation_id"], [])
        t = json.loads(r.pop("transcript_json"))
        r["messages"] = len(t)
        r["preview"] = next((m["text"][:120] for m in t if m["speaker"] == "client"), "")
        last = db.query_one(
            """SELECT id, checklist_version_id, result_json, created_at FROM evaluations
               WHERE group_id=? AND conversation_id=? ORDER BY id DESC LIMIT 1""",
            (group_id, r["conversation_id"]),
        )
        if last:
            from .appeals import final_result_for  # локальный импорт: избегаем цикла

            fin = final_result_for(last["id"])
            r["evaluation"] = {
                "id": last["id"],
                "checklist_version_id": last["checklist_version_id"],
                "created_at": last["created_at"],
                "total": fin["final"]["total"],
                "original_total": fin["original"]["total"],
                "critical_flag": fin["final"]["critical_flag"],
                "open_appeals": fin["open_appeals"],
            }
        else:
            r["evaluation"] = None
        out.append(r)
    return out


# ---------- API ----------

@router.get("/api/datasets/{group_id}/conversations")
async def api_list(group_id: str, refresh: bool = False):
    fetched = None
    cached = db.query_one("SELECT COUNT(*) AS n FROM conversations WHERE group_id=?", (group_id,))["n"]
    if refresh or cached == 0:
        fetched = await fetch_remote(group_id)
    return {"group_id": group_id, "fetched": fetched, "conversations": list_conversations(group_id)}


@router.post("/api/datasets/{group_id}/import")
def api_import(group_id: str, payload: Any = Body(...)):
    """Загрузить транскрипт(ы) из файла: формат ответа API, массив записей или одна запись."""
    if isinstance(payload, dict) and "conversation_id" in payload:
        records = [payload]
    else:
        records = extract_records(payload)
    return {"imported": store_records(group_id, records)}


@router.get("/api/conversations/{group_id}/{conversation_id}")
def api_get(group_id: str, conversation_id: str):
    return get_conversation(group_id, conversation_id)


_PASS_HEADERS = ("content-type", "content-length", "content-range", "accept-ranges")
_MEDIA_TYPES = ("audio/", "video/", "application/octet-stream")
_SRC_RE = re.compile(r"<(?:source|audio)\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.I)
# audio_url -> (прямая ссылка на файл, когда истекает [epoch])
_MEDIA_CACHE: dict[str, tuple[str, float]] = {}


def _media_expiry(media_url: str) -> float:
    """Срок жизни подписанной ссылки (параметр se=...), с запасом 2 мин; иначе 15 мин."""
    import time
    from urllib.parse import parse_qs, urlsplit

    se = parse_qs(urlsplit(media_url).query).get("se", [None])[0]
    if se:
        try:
            return datetime.fromisoformat(se.replace("Z", "+00:00")).timestamp() - 120
        except ValueError:
            pass
    return time.time() + 15 * 60


async def resolve_media_url(client: httpx.AsyncClient, audio_url: str, force: bool = False) -> str:
    """audio_url в наборе может вести на HTML-страницу с плеером (Power Automate), а сам mp3 —
    во вложенном <source src> с подписью на ~1 час. Достаём прямую ссылку и кэшируем до истечения."""
    import html as html_lib
    import time

    cached = _MEDIA_CACHE.get(audio_url)
    if cached and not force and cached[1] > time.time():
        return cached[0]
    async with client.stream("GET", audio_url) as r:
        ctype = r.headers.get("content-type", "").lower()
        if r.status_code < 400 and ctype.startswith(_MEDIA_TYPES):
            media = audio_url  # ссылка уже ведёт на файл
        else:
            body = (await r.aread())[:500_000].decode("utf-8", "replace")
            if r.status_code >= 400:
                raise HTTPException(502, f"Ссылка на запись вернула {r.status_code}")
            m = _SRC_RE.search(body)
            if not m:
                raise HTTPException(502, "На странице записи не найден аудиофайл (<source src>)")
            media = html_lib.unescape(m.group(1))
    _MEDIA_CACHE[audio_url] = (media, _media_expiry(media))
    return media


async def _open_media(client: httpx.AsyncClient, audio_url: str, range_header: Optional[str]) -> httpx.Response:
    headers = {"Range": range_header} if range_header else {}
    for attempt in range(2):
        media = await resolve_media_url(client, audio_url, force=attempt > 0)
        resp = await client.send(client.build_request("GET", media, headers=headers), stream=True)
        if resp.status_code in (401, 403, 404) and attempt == 0:
            await resp.aclose()  # подпись истекла — получаем свежую ссылку
            continue
        return resp
    return resp


@router.get("/api/audio/{group_id}/{conversation_id}")
async def api_audio(group_id: str, conversation_id: str, request: Request):
    """Запись звонка через бэкенд: раскрываем страницу-плеер до mp3, проксируем с Range (перемотка)."""
    row = db.query_one(
        "SELECT audio_url FROM conversations WHERE group_id=? AND conversation_id=?", (group_id, conversation_id)
    )
    if not row or not row["audio_url"]:
        raise HTTPException(404, "У диалога нет записи")
    client = httpx.AsyncClient(timeout=httpx.Timeout(120, read=None), follow_redirects=True)
    try:
        resp = await _open_media(client, row["audio_url"], request.headers.get("range"))
    except HTTPException:
        await client.aclose()
        raise
    except httpx.HTTPError as e:
        await client.aclose()
        raise HTTPException(502, f"Не удалось получить запись: {type(e).__name__}") from e
    if resp.status_code >= 400:
        await resp.aclose()
        await client.aclose()
        raise HTTPException(502, f"Хранилище записи вернуло {resp.status_code}")

    async def body():
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    out_headers = {k: v for k, v in resp.headers.items() if k.lower() in _PASS_HEADERS}
    out_headers.setdefault("accept-ranges", "bytes")
    out_headers["cache-control"] = "private, max-age=600"
    return StreamingResponse(body(), status_code=resp.status_code, headers=out_headers)
