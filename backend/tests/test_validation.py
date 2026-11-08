import json

from app.prompt import build_schema, build_system, build_user
from app.validation import parse_and_validate


def good(**over):
    v = {
        "01": {"evidence": [1], "checked_range": None, "explanation": "Поздоровался.", "answer": "yes"},
        "02": {"evidence": [], "checked_range": None, "explanation": "Данных хватало.", "answer": "na"},
        "03": {"evidence": [], "checked_range": None, "explanation": "Не требовалась.", "answer": "na"},
        "04": {"evidence": [3], "checked_range": None, "explanation": "Частично.", "answer": 15},
        "05": {"evidence": [3], "checked_range": None, "explanation": "Вежлив.", "answer": "yes"},
        "06": {"evidence": [3], "checked_range": None, "explanation": "Один сбой.", "answer": 7},
        "07": {"evidence": [], "checked_range": [4, 5], "explanation": "Не попрощался.", "answer": "no"},
        "08": {"evidence": [], "checked_range": None, "explanation": "Нет.", "answer": "no"},
    }
    v.update(over)
    return json.dumps({"verdicts": v}, ensure_ascii=False)


def test_valid_response(default_config):
    verdicts, errors, warnings = parse_and_validate(good(), default_config, 6)
    assert errors == [] and warnings == []
    assert [v.criterion_id for v in verdicts] == ["01", "02", "03", "04", "05", "06", "07", "08"]
    assert verdicts[6].checked_range == [4, 5]


def test_list_format_and_code_fence(default_config):
    data = json.loads(good())["verdicts"]
    lst = [{"criterion_id": k, **v} for k, v in data.items()]
    text = "```json\n" + json.dumps({"verdicts": lst}) + "\n```"
    verdicts, errors, _ = parse_and_validate(text, default_config, 6)
    assert errors == [] and len(verdicts) == 8


def test_na_where_not_allowed_is_error(default_config):
    _, errors, _ = parse_and_validate(good(**{"01": {"evidence": [], "checked_range": None, "explanation": "x", "answer": "na"}}), default_config, 6)
    assert any("01" in e for e in errors)


def test_invalid_scale_level_is_error(default_config):
    _, errors, _ = parse_and_validate(good(**{"04": {"evidence": [1], "checked_range": None, "explanation": "x", "answer": 12}}), default_config, 6)
    assert any("04" in e for e in errors)


def test_missing_criterion_is_error(default_config):
    data = json.loads(good())
    del data["verdicts"]["05"]
    _, errors, _ = parse_and_validate(json.dumps(data), default_config, 6)
    assert any("05" in e for e in errors)


def test_out_of_range_indices_dropped(default_config):
    text = good(**{"05": {"evidence": [3, 99], "checked_range": None, "explanation": "x", "answer": "yes"}})
    verdicts, errors, warnings = parse_and_validate(text, default_config, 6)
    assert errors == []
    assert verdicts[4].evidence == [3]
    assert warnings


def test_no_without_any_reference_is_error(default_config):
    text = good(**{"07": {"evidence": [], "checked_range": None, "explanation": "x", "answer": "no"}})
    _, errors, _ = parse_and_validate(text, default_config, 6)
    assert any("07" in e for e in errors)


def test_bad_json(default_config):
    _, errors, _ = parse_and_validate("не json", default_config, 6)
    assert errors


def test_prompt_and_schema_follow_config(default_config):
    data = default_config.model_dump()
    data["criteria"][0] = {
        "id": "01N", "section_id": "start", "title": "Проверка ФИО клиента", "description": "Да — оператор назвал клиента по имени.",
        "type": "scale", "weight": 10, "na_allowed": True, "na_condition": "Имя клиента неизвестно",
        "scale": [{"value": 0, "description": "нет"}, {"value": 3, "description": "да"}],
    }
    from app.schemas import ChecklistConfig

    cfg = ChecklistConfig.model_validate(data)
    system = build_system(cfg)
    assert "Проверка ФИО клиента" in system and "Имя клиента неизвестно" in system
    assert "Установление контакта" not in system
    assert '"3", "0", "na"' in system  # допустимые значения нового критерия в промпте
    schema = build_schema(cfg)
    item = schema["properties"]["verdicts"]["items"]["properties"]
    assert "01N" in item["criterion_id"]["enum"] and "01" not in item["criterion_id"]["enum"]
    assert {"3", "0", "na", "yes", "no"} <= set(item["answer"]["enum"])
    # компактность: никаких anyOf и веток на каждый критерий
    assert "anyOf" not in json.dumps(schema)


def test_string_scale_answer_and_empty_range(default_config):
    data = json.loads(good())["verdicts"]
    lst = [{"criterion_id": k, **v} for k, v in data.items()]
    for x in lst:
        x["answer"] = str(x["answer"])
        x["checked_range"] = x["checked_range"] or []
    verdicts, errors, _ = parse_and_validate(json.dumps({"verdicts": lst}), default_config, 6)
    assert errors == []
    assert verdicts[3].answer == 15 and verdicts[5].answer == 7
    assert verdicts[0].checked_range is None and verdicts[6].checked_range == [4, 5]


def test_user_prompt_numbers_messages():
    conv = {"conversation_id": "x", "channel": "agent chat", "language": "KZ",
            "transcript": [{"index": 0, "speaker": "client", "timestamp": "00:01", "text": "Сәлем"},
                           {"index": 1, "speaker": "operator", "timestamp": "00:05", "text": "Сәлеметсіз бе!"}]}
    u = build_user(conv, ["ошибка A"])
    assert "[0] client 00:01: Сәлем" in u and "[1] operator 00:05" in u and "ошибка A" in u
