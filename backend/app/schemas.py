"""Pydantic-модели: конфигурация чек-листа, вердикты LLM, запросы API."""
from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

CriterionType = Literal["binary", "scale", "flag"]
Answer = Union[Literal["yes", "no", "na"], int]


class ScaleLevel(BaseModel):
    value: int = Field(ge=0)
    description: str = ""


class Section(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)


class Criterion(BaseModel):
    id: str = Field(min_length=1)
    section_id: str
    title: str = Field(min_length=1)
    description: str = ""
    type: CriterionType = "binary"
    weight: float = Field(default=0, ge=0)
    na_allowed: bool = False
    na_condition: str = ""
    scale: list[ScaleLevel] = Field(default_factory=list)
    guidance: str = ""
    # код критерия в выгрузке results.json (формат организаторов), напр. "contact"; пусто — используется id
    export_code: str = ""

    @model_validator(mode="after")
    def _check(self) -> "Criterion":
        if self.type == "scale":
            if len(self.scale) < 2:
                raise ValueError(f"Критерий {self.id}: у шкалы должно быть минимум 2 ступени")
            values = [lvl.value for lvl in self.scale]
            if len(set(values)) != len(values):
                raise ValueError(f"Критерий {self.id}: ступени шкалы должны быть уникальны")
            if max(values) <= 0:
                raise ValueError(f"Критерий {self.id}: максимум шкалы должен быть > 0")
            # ступени храним по убыванию
            self.scale = sorted(self.scale, key=lambda l: l.value, reverse=True)
        else:
            self.scale = []
        if self.type == "flag":
            self.weight = 0
            self.na_allowed = False
        if not self.na_allowed:
            self.na_condition = ""
        return self

    @property
    def scale_max(self) -> int:
        return max((l.value for l in self.scale), default=0)

    def allowed_answers(self) -> list:
        if self.type == "scale":
            vals: list = [l.value for l in self.scale]
        else:
            vals = ["yes", "no"]
        if self.na_allowed:
            vals.append("na")
        return vals


class ChecklistConfig(BaseModel):
    name: str = Field(min_length=1)
    instructions: str = ""
    sections: list[Section]
    criteria: list[Criterion]

    @model_validator(mode="after")
    def _check(self) -> "ChecklistConfig":
        sec_ids = [s.id for s in self.sections]
        if len(set(sec_ids)) != len(sec_ids):
            raise ValueError("Идентификаторы секций должны быть уникальны")
        crit_ids = [c.id for c in self.criteria]
        if len(set(crit_ids)) != len(crit_ids):
            raise ValueError("Идентификаторы критериев должны быть уникальны")
        if not self.criteria:
            raise ValueError("В чек-листе должен быть хотя бы один критерий")
        for c in self.criteria:
            if c.section_id not in sec_ids:
                raise ValueError(f"Критерий {c.id}: секция '{c.section_id}' не найдена")
        return self

    def criterion(self, cid: str) -> Optional[Criterion]:
        return next((c for c in self.criteria if c.id == cid), None)

    @property
    def total_weight(self) -> float:
        return sum(c.weight for c in self.criteria if c.type != "flag")


class Verdict(BaseModel):
    criterion_id: str
    answer: Answer
    explanation: str = ""
    evidence: list[int] = Field(default_factory=list)
    checked_range: Optional[list[int]] = None

    @field_validator("answer", mode="before")
    @classmethod
    def _norm_answer(cls, v):
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("n/a", "na", "n.a."):
                return "na"
            if s in ("yes", "no"):
                return s
            if s.lstrip("-").isdigit():
                return int(s)
        if isinstance(v, float) and v.is_integer():
            return int(v)
        return v


# ---------- запросы API ----------

class EvaluateRequest(BaseModel):
    group_id: str
    conversation_id: str
    checklist_version_id: int


class AppealCreate(BaseModel):
    evaluation_id: int
    criterion_id: str
    comment: str = Field(min_length=1)


class AppealDecision(BaseModel):
    decision: Literal["confirm", "change"]
    new_answer: Optional[Answer] = None
    reason: str = Field(min_length=1)

    @field_validator("new_answer", mode="before")
    @classmethod
    def _norm(cls, v):
        return Verdict._norm_answer(v)


class BatchRequest(BaseModel):
    group_id: str
    checklist_version_id: int
    only_missing: bool = False
    conversation_ids: Optional[list[str]] = None
