import pytest

from app.schemas import ChecklistConfig
from app.scoring import ScoringError, compute


def answers(**kw):
    base = {"01": "yes", "02": "yes", "03": "yes", "04": 20, "05": "yes", "06": 10, "07": "yes", "08": "no"}
    base.update({k.lstrip("c"): v for k, v in kw.items()})
    return base


def test_reference_example_from_task(default_config):
    # 02 и 03 = N/A → применимый вес 65; 10 + 15 + 15 + 10.5 + 5 = 55.5 → 85.38
    r = compute(default_config, answers(c02="na", c03="na", c04=15, c06=7))
    assert r["applicable_weight"] == 65
    assert r["earned"] == 55.5
    assert r["total"] == 85.38
    pts = {c["criterion_id"]: c["points"] for c in r["criteria"]}
    assert pts == {"01": 10, "02": None, "03": None, "04": 15, "05": 15, "06": 10.5, "07": 5, "08": None}


def test_all_yes_is_100(default_config):
    r = compute(default_config, answers())
    assert r["total"] == 100
    assert r["critical_flag"] is False


def test_all_no_is_0(default_config):
    r = compute(default_config, answers(c01="no", c02="no", c03="no", c04=0, c05="no", c06=0, c07="no"))
    assert r["total"] == 0


def test_critical_flag_does_not_affect_score(default_config):
    r = compute(default_config, answers(c08="yes"))
    assert r["total"] == 100
    assert r["critical_flag"] is True
    assert r["critical_criteria"] == ["08"]
    crit_sec = next(s for s in r["sections"] if s["section_id"] == "critical")
    assert crit_sec["score"] is None and crit_sec["flags"] == ["08"]


def test_na_not_allowed_raises(default_config):
    with pytest.raises(ScoringError):
        compute(default_config, answers(c01="na"))
    with pytest.raises(ScoringError):
        compute(default_config, answers(c06="na"))


def test_invalid_scale_level_raises(default_config):
    with pytest.raises(ScoringError):
        compute(default_config, answers(c04=12))
    with pytest.raises(ScoringError):
        compute(default_config, answers(c04="yes"))


def test_missing_answer_raises(default_config):
    a = answers()
    del a["05"]
    with pytest.raises(ScoringError):
        compute(default_config, a)


def test_no_applicable_weight_gives_null():
    cfg = ChecklistConfig.model_validate(
        {
            "name": "t",
            "sections": [{"id": "s", "title": "S"}],
            "criteria": [
                {"id": "a", "section_id": "s", "title": "A", "type": "binary", "weight": 50, "na_allowed": True, "na_condition": "x"},
                {"id": "f", "section_id": "s", "title": "F", "type": "flag"},
            ],
        }
    )
    r = compute(cfg, {"a": "na", "f": "no"})
    assert r["total"] is None
    assert r["sections"][0]["score"] is None


def test_section_scores(default_config):
    r = compute(default_config, answers(c02="na", c03="na", c04=15, c06=7))
    sec = {s["section_id"]: s for s in r["sections"]}
    # Начало: только 01 применим, 10/10
    assert sec["start"]["score"] == 100 and sec["start"]["applicable_weight"] == 10
    # Безопасность: 04 = 15/20
    assert sec["security"]["score"] == 75
    # Ведение: 15 + 10.5 + 5 = 30.5 / 35
    assert sec["conduct"]["earned"] == 30.5
    assert sec["conduct"]["score"] == 87.14


def test_scale_points_proportional():
    cfg = ChecklistConfig.model_validate(
        {
            "name": "t",
            "sections": [{"id": "s", "title": "S"}],
            "criteria": [
                {"id": "q", "section_id": "s", "title": "Q", "type": "scale", "weight": 30,
                 "scale": [{"value": 0}, {"value": 2}, {"value": 5}]},
                {"id": "b", "section_id": "s", "title": "B", "type": "binary", "weight": 70},
            ],
        }
    )
    r = compute(cfg, {"q": 2, "b": "yes"})
    assert r["criteria"][0]["points"] == 12  # 2/5*30
    assert r["total"] == 82


def test_new_criterion_via_config_only(default_config):
    data = default_config.model_dump()
    data["criteria"].append(
        {"id": "09", "section_id": "conduct", "title": "Новый", "type": "binary", "weight": 10}
    )
    cfg = ChecklistConfig.model_validate(data)
    r = compute(cfg, {**answers(), "09": "no"})
    assert r["applicable_weight"] == 110
    assert r["total"] == 90.91
