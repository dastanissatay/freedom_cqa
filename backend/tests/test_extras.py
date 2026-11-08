"""Выгрузка results.json, маскирование ПДн, сводка по набору, проверка стабильности (mock-модель)."""
import json

import pytest
from fastapi.testclient import TestClient

from app.export import renumber_refs, validate_results
from app.main import app
from app.pii import mask
from app.prompt import build_user
from app.schemas import ChecklistConfig

GID = "EXT"
DATA = {"isSuccess": True, "data": {"records": [
    {"conversation_id": "e1", "channel": "agent chat", "language": "RU", "audio_url": None,
     "transcript": [{"timestamp": "00:00", "speaker": "client", "text": "Мой ИИН 900101300123, карта 4400 4301 2345 6789"},
                    {"timestamp": "00:10", "speaker": "operator", "text": "Здравствуйте! Меня зовут Анна."},
                    {"timestamp": "00:30", "speaker": "operator", "text": "Готово. До свидания!"}]},
    {"conversation_id": "e2", "channel": "agent chat", "language": "KZ", "audio_url": None,
     "transcript": [{"timestamp": "00:00", "speaker": "client", "text": "Сұрақ"},
                    {"timestamp": "00:10", "speaker": "operator", "text": "Жауап"}]},
    {"conversation_id": "e3", "channel": "agent chat", "language": "EN", "audio_url": None,
     "transcript": [{"timestamp": "00:00", "speaker": "client", "text": "Hi"}]},
]}}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.post(f"/api/datasets/{GID}/import", json=DATA)
        yield c


@pytest.fixture(scope="module")
def vid(client):
    return client.get("/api/checklists").json()[0]["versions"][-1]["id"]


# ---------- маскирование ----------

def test_mask_pii():
    s = mask("ИИН 900101300123, карта 4400 4301 2345 6789, тел +7 701 234 56 78, a.b@mail.kz, KZ86 125K ZT50 0410 0100")
    assert "900101300123" not in s and "4400" not in s and "701" not in s and "mail.kz" not in s
    assert all(t in s for t in ("[ИИН]", "[КАРТА]", "[ТЕЛЕФОН]", "[EMAIL]", "[IBAN]"))
    assert mask("Комиссия 1500 тенге, 5 сделок, 00:40") == "Комиссия 1500 тенге, 5 сделок, 00:40"


def test_prompt_masks_only_when_enabled():
    conv = {"conversation_id": "x", "transcript": [{"index": 0, "speaker": "client", "timestamp": "00:00", "text": "ИИН 900101300123"}]}
    assert "900101300123" in build_user(conv)
    assert "[ИИН]" in build_user(conv, mask_pii=True) and "900101300123" not in build_user(conv, mask_pii=True)


# ---------- выгрузка ----------

def test_renumber_refs():
    assert renumber_refs("Основание — [0] и [9].") == "Основание — [1] и [10]."


def test_results_export_format(client, vid):
    for cid in ("e1", "e2"):  # e3 намеренно не оцениваем
        client.post("/api/evaluations", json={"group_id": GID, "conversation_id": cid, "checklist_version_id": vid})
    doc = client.get(f"/api/export/{GID}?checklist_version_id={vid}").json()
    assert doc["schema_version"] == "1.0" and doc["team_name"] and doc["model"]["mode"] in ("local", "external_api", "hybrid")
    res = {r["conversation_id"]: r for r in doc["results"]}
    assert set(res) == {"e1", "e2", "e3"}
    assert res["e3"]["status"] == "error" and res["e3"]["criteria"] == [] and res["e3"]["score"] is None and res["e3"]["error"]
    e1 = res["e1"]
    codes = [c["criterion_id"] for c in e1["criteria"]]
    assert codes == ["contact", "discovery", "identification", "consultation", "empathy", "dialogue", "closure", "critical"]
    vals = {c["criterion_id"]: c["value"] for c in e1["criteria"]}
    assert vals["contact"] is True and vals["discovery"] is None and vals["consultation"] == 20 and vals["critical"] is False
    # mock ссылается на первую реплику оператора (индекс 1) → в формате организаторов это реплика 2
    assert next(c for c in e1["criteria"] if c["criterion_id"] == "contact")["evidence_turns"] == [2]
    assert e1["score"]["critical_flag"] is False and isinstance(e1["score"]["final_score"], float)
    cfg = ChecklistConfig.model_validate(client.get(f"/api/checklists/versions/{vid}").json()["config"])
    assert validate_results(doc, cfg) == []
    assert "e1" in client.get(f"/api/export/{GID}?fmt=csv").text


def test_swapped_criterion_exported_by_config(client, vid):
    """Замена критерия через конфигурацию: новый код выгрузки без правки кода."""
    cfg = client.get(f"/api/checklists/versions/{vid}").json()["config"]
    cfg["name"] = "Swap-тест"
    cfg["criteria"][4] = {"id": "05s", "section_id": "conduct", "title": "Персонализация", "type": "binary",
                          "weight": 15, "export_code": "personalization"}
    new = client.post("/api/checklists", json={"config": cfg}).json()
    client.post("/api/evaluations", json={"group_id": GID, "conversation_id": "e2", "checklist_version_id": new["id"]})
    doc = client.get(f"/api/export/{GID}?checklist_version_id={new['id']}").json()
    e2 = next(r for r in doc["results"] if r["conversation_id"] == "e2")
    codes = [c["criterion_id"] for c in e2["criteria"]]
    assert "personalization" in codes and "empathy" not in codes
    assert validate_results(doc, ChecklistConfig.model_validate(cfg)) == []


def test_validate_results_catches_errors(default_config):
    good = {"conversation_id": "a", "status": "ok", "score": {"final_score": 100.0, "critical_flag": False}, "criteria": [
        {"criterion_id": k, "value": v, "rationale": "", "evidence_turns": []} for k, v in
        [("contact", True), ("discovery", None), ("identification", None), ("consultation", 20), ("empathy", True),
         ("dialogue", 10), ("closure", True), ("critical", False)]]}
    assert validate_results({"results": [good]}, default_config) == []
    bad = json.loads(json.dumps(good))
    bad["criteria"][0]["value"] = None  # contact не допускает N/A
    bad["criteria"][5]["value"] = 5     # dialogue: нет ступени 5
    bad["score"]["critical_flag"] = True
    errs = validate_results({"results": [bad, good]}, default_config)
    assert len(errs) == 4  # два недопустимых значения, флаг, повтор ID


# ---------- сводка и стабильность ----------

def test_summary(client, vid):
    s = client.get(f"/api/summary?group_id={GID}&checklist_version_id={vid}").json()
    assert s["dialogs_total"] == 3 and s["evaluated"] == 2
    assert sum(b["n"] for b in s["distribution"]) == 2
    assert {x["key"] for x in s["by_language"]} == {"RU", "KZ"}
    c07 = next(c for c in s["criteria"] if c["criterion_id"] == "07")
    assert c07["applicable"] == 2 and c07["fail"] == 1 and c07["fail_pct"] == 50  # e2 без прощания (mock)
    assert s["top_problems"][0]["fail_pct"] >= c07["fail_pct"]


def test_stability(client, vid):
    eid = client.get(f"/api/evaluations?group_id={GID}&conversation_id=e1").json()[0]["id"]
    r = client.post(f"/api/evaluations/{eid}/stability?runs=2").json()
    assert r["runs"] == 2 and r["agreement_pct"] == 100 and all(len(c["answers"]) == 3 for c in r["criteria"])
    assert client.get(f"/api/evaluations/{eid}").json()["usage"]["stability"]["stable"] == 8
