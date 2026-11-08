import time

import pytest
from fastapi.testclient import TestClient

from app.dataset import normalize_transcript
from app.main import app

DATA = {
    "error": "",
    "isSuccess": True,
    "data": {
        "dataset_group_id": "T1",
        "total_records": 2,
        "records": [
            {
                "conversation_id": "chat-1", "channel": "agent chat", "language": "RU", "audio_url": None,
                "transcript": [
                    {"timestamp": "00:02", "speaker": "client", "text": "Как пополнить счёт?"},
                    {"timestamp": "00:10", "speaker": "bot", "text": "Соединяю с оператором"},
                    {"timestamp": "00:40", "speaker": "operator", "text": "Здравствуйте! Меня зовут Анна."},
                    {"timestamp": "01:20", "speaker": "operator", "text": "Пополнить можно через приложение."},
                    {"timestamp": "02:00", "speaker": "system", "text": "Чат закрыт"},
                ],
            },
            {
                "conversation_id": "call-1", "channel": "voice call", "language": "EN", "audio_url": "https://example.com/a.mp3",
                "transcript": [
                    {"timestamp": "2025-01-10T10:00:00+05:00", "speaker": "operator", "text": "Hello, this is Tom."},
                    {"timestamp": "2025-01-10T10:00:12.5+05:00", "speaker": "client", "text": "Hi"},
                    {"timestamp": "2025-01-10T10:01:30+05:00", "speaker": "operator", "text": "Goodbye, have a nice day"},
                ],
            },
        ],
    },
}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_dataset_url_keeps_existing_params():
    from app.dataset import build_dataset_url

    u = build_dataset_url("https://x.test/invoke?api-version=1&sp=%2Ftriggers&sig=abc&group_id=DEV", "CTRL-7")
    assert "api-version=1" in u and "sig=abc" in u and "sp=%2Ftriggers" in u
    assert u.count("group_id=") == 1 and "group_id=CTRL-7" in u
    assert build_dataset_url("https://x.test/api", "DEV") == "https://x.test/api?group_id=DEV"


def test_audio_page_parsing():
    from datetime import datetime, timezone

    from app.dataset import _SRC_RE, _media_expiry

    page = '<audio id="a" controls>\n<source src="https://h/f.mp3?se=2026-11-07T10:06:09Z&amp;sig=x" type="audio/mpeg"></audio>'
    url = _SRC_RE.search(page).group(1)
    assert url.startswith("https://h/f.mp3")
    import html

    exp = _media_expiry(html.unescape(url))
    assert exp == datetime(2026, 11, 7, 10, 4, 9, tzinfo=timezone.utc).timestamp()  # se − 2 мин


def test_offsets():
    t = normalize_transcript(DATA["data"]["records"][1]["transcript"])
    assert [m["offset_sec"] for m in t] == [0, 12.5, 90]
    assert t[0]["timestamp"] == "2025-01-10T10:00:00+05:00"  # исходная метка сохранена
    c = normalize_transcript(DATA["data"]["records"][0]["transcript"])
    assert c[2]["offset_sec"] == 38


def test_full_flow(client):
    assert client.post("/api/datasets/T1/import", json=DATA).json()["imported"] == 2
    convs = client.get("/api/datasets/T1/conversations").json()["conversations"]
    assert {c["conversation_id"] for c in convs} == {"chat-1", "call-1"}

    cl = client.get("/api/checklists").json()
    vid = cl[0]["latest_version_id"]

    ev = client.post("/api/evaluations", json={"group_id": "T1", "conversation_id": "chat-1", "checklist_version_id": vid}).json()
    assert ev["original"]["total"] is not None
    assert len(ev["verdicts"]) == 8
    orig_total = ev["final"]["total"]

    # апелляция по 04
    ap = client.post("/api/appeals", json={"evaluation_id": ev["id"], "criterion_id": "04", "comment": "Не согласен"}).json()
    assert ap["status"] == "open" and ap["original_answer"] == 20
    assert client.post("/api/appeals", json={"evaluation_id": ev["id"], "criterion_id": "04", "comment": "ещё"}).status_code == 409

    # недопустимое значение
    bad = client.post(f"/api/appeals/{ap['id']}/decision", json={"decision": "change", "new_answer": 12, "reason": "x"})
    assert bad.status_code == 400

    res = client.post(f"/api/appeals/{ap['id']}/decision", json={"decision": "change", "new_answer": 10, "reason": "Пропущен вопрос"}).json()
    assert res["appeal"]["status"] == "resolved"
    assert res["original"]["total"] == orig_total
    assert res["final"]["total"] < orig_total
    assert res["overrides"] == {"04": 10}

    full = client.get(f"/api/evaluations/{ev['id']}").json()
    assert full["final"]["total"] == res["final"]["total"] and full["appeals"][0]["reviewer_reason"] == "Пропущен вопрос"


def test_new_checklist_version_applies(client):
    cl = client.get("/api/checklists").json()[0]
    cfg = client.get(f"/api/checklists/versions/{cl['latest_version_id']}").json()["config"]
    cfg["criteria"][4] = {"id": "05b", "section_id": "conduct", "title": "Персонализация", "description": "Да — обратился по имени",
                          "type": "binary", "weight": 15, "na_allowed": False}
    cfg["name"] = "Чек-лист с заменой"
    new = client.post("/api/checklists", json={"config": cfg, "comment": "замена 05"}).json()
    ev = client.post("/api/evaluations", json={"group_id": "T1", "conversation_id": "call-1", "checklist_version_id": new["id"]}).json()
    ids = [v["criterion_id"] for v in ev["verdicts"]]
    assert "05b" in ids and "05" not in ids
    assert ev["checklist"]["name"] == "Чек-лист с заменой"


def test_invalid_checklist_rejected(client):
    bad = {"name": "x", "sections": [{"id": "s", "title": "S"}],
           "criteria": [{"id": "a", "section_id": "nope", "title": "A", "type": "binary", "weight": 1}]}
    assert client.post("/api/checklists", json={"config": bad}).status_code == 422


def test_batch(client):
    vid = client.get("/api/checklists").json()[0]["latest_version_id"]
    job = client.post("/api/batch", json={"group_id": "T1", "checklist_version_id": vid}).json()
    for _ in range(100):
        st = client.get(f"/api/batch/{job['id']}").json()
        if st["status"] != "running":
            break
        time.sleep(0.05)
    assert st["status"] == "done" and st["done"] == 2 and st["failed"] == 0
    csv = client.get(f"/api/export/T1?checklist_version_id={vid}").text
    assert "chat-1" in csv and "call-1" in csv
