"""Эталонная разметка и отчёт о согласии LLM с эталоном (на mock-модели)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.references import _kappa

GID = "REF"
DATA = {
    "isSuccess": True,
    "data": {"records": [
        {"conversation_id": "r1", "channel": "agent chat", "language": "RU", "audio_url": None,
         "transcript": [{"timestamp": "00:00", "speaker": "client", "text": "Вопрос"},
                        {"timestamp": "00:10", "speaker": "operator", "text": "Здравствуйте! Меня зовут Анна."},
                        {"timestamp": "00:30", "speaker": "operator", "text": "Ответ. До свидания!"}]},
        {"conversation_id": "r2", "channel": "agent chat", "language": "RU", "audio_url": None,
         "transcript": [{"timestamp": "00:00", "speaker": "client", "text": "Вопрос"},
                        {"timestamp": "00:10", "speaker": "operator", "text": "Ответ по существу"}]},
    ]},
}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.post(f"/api/datasets/{GID}/import", json=DATA)
        yield c


@pytest.fixture(scope="module")
def vid(client):
    # отдельный чек-лист, чтобы не зависеть от других тестов
    base = client.get("/api/checklists").json()[0]
    cfg = client.get(f"/api/checklists/versions/{base['versions'][-1]['id']}").json()["config"]
    cfg["name"] = "Для теста эталона"
    return client.post("/api/checklists", json={"config": cfg}).json()["id"]


def full(**over):
    a = {"01": "yes", "02": "na", "03": "na", "04": 20, "05": "yes", "06": 10, "07": "yes", "08": "no"}
    a.update({k.lstrip("c"): v for k, v in over.items()})
    return a


def test_kappa():
    assert _kappa([("yes", "yes"), ("no", "no")]) == 1.0
    assert _kappa([("yes", "yes"), ("yes", "yes")]) == 1.0
    assert _kappa([("yes", "no"), ("no", "yes")]) == -1.0
    assert _kappa([]) is None


def test_reference_validation(client, vid):
    url = f"/api/references/{GID}/r1"
    partial = {k: v for k, v in full().items() if k != "05"}
    assert client.put(url, json={"checklist_version_id": vid, "answers": partial}).status_code == 400
    assert client.put(url, json={"checklist_version_id": vid, "answers": full(c01="na")}).status_code == 400
    assert client.put(url, json={"checklist_version_id": vid, "answers": full(c04=12)}).status_code == 400
    # числа строкой допустимы (как из формы/JSON-импорта)
    r = client.put(url, json={"checklist_version_id": vid, "answers": full(c04="15"), "notes": {"04": "деталь"}}).json()
    assert r["answers"]["04"] == 15 and r["notes"] == {"04": "деталь"} and r["total"] == 92.31


def test_quality_report(client, vid):
    # эталон: r1 полностью совпадает с mock; r2 — расходится по 04 и 08
    client.put(f"/api/references/{GID}/r1", json={"checklist_version_id": vid, "answers": full()})
    imp = client.post("/api/references/import", json={
        "group_id": GID, "checklist_version_id": vid,
        "items": [{"conversation_id": "r2", "answers": full(c01="no", c04=10, c07="no", c08="yes")},
                  {"conversation_id": "nope", "answers": {"01": "yes"}}]}).json()
    assert imp["saved"] == 1 and "nope" in imp["errors"]

    q = client.get(f"/api/quality?group_id={GID}&checklist_version_id={vid}").json()
    assert q["references"] == 2 and q["compared"] == 0 and set(q["missing_evaluation"]) == {"r1", "r2"}

    for cid in ("r1", "r2"):
        client.post("/api/evaluations", json={"group_id": GID, "conversation_id": cid, "checklist_version_id": vid})
    q = client.get(f"/api/quality?group_id={GID}&checklist_version_id={vid}").json()
    assert q["compared"] == 2
    # mock на r2: 01=no, 04=20, 07=no, 08=no → расхождения по 04 и 08 → 14/16 совпадений
    assert q["summary"]["criteria_agreement_pct"] == 87.5
    dis = {(d["conversation_id"], d["criterion_id"]) for d in q["disagreements"]}
    assert dis == {("r2", "04"), ("r2", "08")}
    c04 = next(c for c in q["criteria"] if c["criterion_id"] == "04")
    assert c04["agreement_pct"] == 50 and c04["scale_mae_pct"] == 25 and c04["confusion"] == {"20→10": 1}
    assert q["summary"]["critical_flag"] == {"tn": 1, "fn": 1}
    r1 = next(d for d in q["dialogs"] if d["conversation_id"] == "r1")
    assert r1["diff"] == 0 and r1["agree"] == 8

    lst = client.get(f"/api/datasets/{GID}/conversations").json()["conversations"]
    assert all(vid in c["reference_versions"] for c in lst)
