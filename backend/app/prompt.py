"""Динамическая генерация промпта и JSON-схемы ответа из конфигурации чек-листа.

Ничего о конкретных критериях здесь не захардкожено: новый/заменённый критерий
попадает в промпт и схему автоматически.
"""
from __future__ import annotations

from . import pii
from .schemas import ChecklistConfig, Criterion

TYPE_LABEL = {
    "binary": "Да/Нет",
    "scale": "числовая шкала",
    "flag": "флаг Да/Нет (без баллов)",
}

SYSTEM_TEMPLATE = """Ты — эксперт службы контроля качества брокерской компании. Ты оцениваешь работу ОПЕРАТОРА поддержки по транскрипту диалога (чат или звонок) строго по чек-листу.

# Общие правила
- Оценивай только реплики оператора (speaker = operator). Реплики клиента (client), бота (bot) и системные события (system) — контекст.
- Каждая реплика транскрипта пронумерована: [i] speaker timestamp: text. Ссылайся на реплики ТОЛЬКО по этим номерам i.
- evidence — номера реплик, которые подтверждают ответ (реплики оператора, клиента, бота или системные сообщения). Для каждого критерия укажи хотя бы одну реплику, если она есть.
- checked_range — [начало, конец] — участок разговора (номера реплик), где ожидалось действие. Обязателен, если ответ «no» или балл шкалы ниже максимального из-за пропущенного действия. Иначе пустой массив [].
- Для N/A ("na") объясни, какое именно условие неприменимости выполнено. N/A можно ставить только критериям, где он разрешён.
- explanation — 1–3 предложения на РУССКОМ языке (даже если диалог на казахском или английском): вердикт, что сделал/не сделал оператор, и на какие реплики опираешься (например: «Основание — [5] и системное сообщение [9]»).
- answer — строка: для Да/Нет — "yes" или "no"; для N/A — "na"; для шкальных критериев — одно из чисел-ступеней шкалы строкой (например "15").
- Персональные данные в транскрипте могут быть заменены метками [ИИН], [КАРТА], [IBAN], [ТЕЛЕФОН], [EMAIL], [НОМЕР]. Метка означает, что в реплике были такие данные: учитывай сам факт их запроса или раскрытия, маскирование — не ошибка оператора.
- Баллы и итог НЕ считай — это сделает система.

# Пояснения к чек-листу
{instructions}

# Чек-лист «{name}»
{criteria}

# Формат ответа
Верни только JSON-объект вида {{"verdicts": [{{"criterion_id": "...", "evidence": [...], "checked_range": [a, b] или [], "explanation": "...", "answer": "..."}}, ...]}} — ровно по одному элементу для КАЖДОГО критерия, в порядке: {ids}."""


def render_criterion(c: Criterion, section_title: str) -> str:
    lines = [f"## Критерий {c.id}. {c.title}  (секция «{section_title}»)"]
    lines.append(f"- Тип ответа: {TYPE_LABEL[c.type]}" + (f"; вес {c.weight:g}" if c.type != "flag" else ""))
    if c.description:
        lines.append(f"- Что проверяем: {c.description}")
    if c.type == "scale":
        lines.append("- Ступени шкалы (answer — одно из чисел):")
        for lvl in c.scale:
            lines.append(f"  - {lvl.value} — {lvl.description}")
    if c.na_allowed:
        lines.append(f"- N/A разрешён. Условие N/A: {c.na_condition or 'критерий неприменим к диалогу'}")
    else:
        lines.append("- N/A НЕ разрешён: всегда выбирай конкретный ответ.")
    if c.guidance:
        lines.append(f"- Уточнение: {c.guidance}")
    lines.append(f"- Допустимые значения answer: {', '.join(f'"{a}"' for a in c.allowed_answers())}")
    return "\n".join(lines)


def build_system(cfg: ChecklistConfig) -> str:
    titles = {s.id: s.title for s in cfg.sections}
    blocks = []
    for s in cfg.sections:
        for c in cfg.criteria:
            if c.section_id == s.id:
                blocks.append(render_criterion(c, titles[s.id]))
    return SYSTEM_TEMPLATE.format(
        instructions=cfg.instructions.strip() or "—",
        name=cfg.name,
        criteria="\n\n".join(blocks),
        ids=", ".join(c.id for c in cfg.criteria),
    )


def render_transcript(transcript: list[dict], mask_pii: bool = False) -> str:
    def text(m: dict) -> str:
        return pii.mask(m["text"]) if mask_pii else m["text"]

    return "\n".join(
        f"[{m['index']}] {m['speaker']} {m.get('timestamp') or ''}: {text(m)}".replace("\n", " ")
        for m in transcript
    )


def build_user(conversation: dict, retry_errors: list[str] | None = None, mask_pii: bool = False) -> str:
    parts = [
        f"Диалог {conversation['conversation_id']} · канал: {conversation.get('channel') or '?'} · язык: {conversation.get('language') or '?'}",
        f"Всего реплик: {len(conversation['transcript'])} (номера 0–{len(conversation['transcript']) - 1}).",
        "",
        "<transcript>",
        render_transcript(conversation["transcript"], mask_pii),
        "</transcript>",
        "",
        "Оцени оператора по каждому критерию чек-листа и верни JSON.",
    ]
    if retry_errors:
        parts += [
            "",
            "ВНИМАНИЕ: предыдущий ответ не прошёл проверку. Исправь ошибки:",
            *[f"- {e}" for e in retry_errors],
        ]
    return "\n".join(parts)


def answer_vocabulary(cfg: ChecklistConfig) -> list[str]:
    """Все допустимые значения answer по чек-листу (строками): yes/no/na и ступени шкал."""
    vocab: list[str] = []
    for c in cfg.criteria:
        for a in c.allowed_answers():
            s = str(a)
            if s not in vocab:
                vocab.append(s)
    return vocab


def build_schema(cfg: ChecklistConfig) -> dict:
    """Компактная JSON Schema для structured outputs.

    Один тип элемента массива (без anyOf и без отдельной ветки на каждый критерий), чтобы
    грамматика оставалась маленькой при любом размере чек-листа. Соответствие конкретного
    answer конкретному критерию, наличие всех критериев и индексы реплик проверяет validation.py
    (при ошибке — повторный вызов с перечнем ошибок).
    """
    item = {
        "type": "object",
        "properties": {
            "criterion_id": {"type": "string", "enum": [c.id for c in cfg.criteria]},
            "evidence": {"type": "array", "items": {"type": "integer"}},
            "checked_range": {"type": "array", "items": {"type": "integer"}},
            "explanation": {"type": "string"},
            "answer": {"type": "string", "enum": answer_vocabulary(cfg)},
        },
        "required": ["criterion_id", "evidence", "checked_range", "explanation", "answer"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"verdicts": {"type": "array", "items": item}},
        "required": ["verdicts"],
        "additionalProperties": False,
    }
