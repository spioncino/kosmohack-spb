from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evarisk.timeutil import ensure_utc

Kind = Literal["observation", "external_forecast", "computed"]
PublishedAtKind = Literal["exact", "derived", "unknown"]
Availability = Literal["available", "missing", "stale", "unusable_for_replay"]
Level = Literal["none", "elevated", "high", "critical", "unknown"]


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def _utc(cls, value: object) -> object:
        if isinstance(value, datetime):
            return ensure_utc(value)
        return value


class Provenance(Frozen):
    raw_id: str
    source: str
    product: str
    published_at: datetime | None
    published_at_kind: PublishedAtKind
    data_version: str
    url: str


class Interval(Frozen):
    start: datetime
    end: datetime
    level: Level
    kind: Kind
    provenance: tuple[Provenance, ...]
    rule: str
    note: str = ""
    start_uncertainty: timedelta | None = None
    end_uncertainty: timedelta | None = None

    @model_validator(mode="after")
    def _ordered(self) -> "Interval":
        if self.end <= self.start:
            raise ValueError("end должен быть позже start")
        return self


class Confidence(Frozen):
    grade: Literal["low", "medium", "high"]
    depends_on: str


class LineAssessment(Frozen):
    line: str
    mechanism: str
    intervals: tuple[Interval, ...]
    availability: Availability
    confidence: Confidence
    limitations: tuple[str, ...]
    chain_ids: tuple[str, ...] = ()
    metrics: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _no_quiet_without_data(self) -> "LineAssessment":
        if self.availability != "available" and any(i.level == "none" for i in self.intervals):
            raise ValueError("уровень none недопустим при отсутствии данных")
        return self


class Window(Frozen):
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _ordered(self) -> "Window":
        if self.end <= self.start:
            raise ValueError("end должен быть позже start")
        return self

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


class WindowScore(Frozen):
    window: Window
    per_line: dict[str, dict[Level, float]]
    completeness: float
    excluded_reason: str | None = None


class Recommendation(Frozen):
    preferred: Window | None
    reason: str
    equivalents: tuple[Window, ...]
    insufficient: bool
    rules_applied: tuple[str, ...]
    # Объяснение обычными словами для человека (D76): первая фраза это решение, дальше почему; `reason` остаётся
    # техническим текстом с кодами решений для аудита. У расчётов до D76 поле пустое.
    explanation: tuple[str, ...] = ()
