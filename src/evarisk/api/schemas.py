from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from evarisk.db.jobs import Job
from evarisk.db.source_state import SourceState, State
from evarisk.replay.verify import Verification
from evarisk.timeutil import ensure_utc
from evarisk.types import (
    Availability,
    Frozen,
    Level,
    LineAssessment,
    Provenance,
    Recommendation,
    Window,
    WindowScore,
)
from evarisk.windows.compare import Constraints as Constraints

Mode = Literal["current", "review", "replay"]
TrajectoryMode = Literal["current", "review"]
TRAJECTORY_MIN_SPAN = timedelta(minutes=1)
TRAJECTORY_MAX_SPAN = timedelta(hours=48)
# Шаг сетки окон 15 мин (config/windows.toml): отрезок начал не короче шага даёт не меньше двух окон.
MIN_START_SPAN = timedelta(minutes=15)
MAX_START_SPAN = timedelta(hours=24)


class AssessmentRequest(Frozen):
    mode: Mode
    cutoff: datetime | None = None
    eva_start: datetime
    duration_hours: float = Field(ge=1.0, le=8.0)
    search_start: datetime
    search_end: datetime
    constraints: Constraints | None = None

    @field_validator("cutoff", "eva_start", "search_start", "search_end", mode="after")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else ensure_utc(value)

    @model_validator(mode="after")
    def _consistent(self) -> "AssessmentRequest":
        if self.mode == "replay" and self.cutoff is None:
            raise ValueError("режим replay требует cutoff")
        if self.mode != "replay" and self.cutoff is not None:
            raise ValueError("cutoff допустим только в режиме replay")
        # Прогноз задним числом не проверяет ничего: отсечение позже начала ВКД видит уже наступившие события (D80).
        if self.cutoff is not None and self.cutoff > self.eva_start:
            raise ValueError(
                "момент отсечения cutoff позже начала ВКД eva_start: прогноз на момент после начала выхода это "
                "расчёт задним числом; cutoff должен быть не позже eva_start"
            )
        # Период поиска задаёт допустимые моменты начала (глоссарий постановки): начало окна от search_start до
        # search_end − длительность. Этот отрезок от одного шага сетки (не меньше двух окон) до 24 часов.
        latest_start = self.search_end - timedelta(hours=self.duration_hours)
        starts = latest_start - self.search_start
        if starts < MIN_START_SPAN or starts > MAX_START_SPAN:
            raise ValueError(
                "допустимые моменты начала (от search_start до search_end минус длительность) должны занимать от "
                "15 минут до 24 часов: постановка требует не менее двух окон и период поиска до 24 часов"
            )
        if not self.search_start <= self.eva_start <= latest_start:
            raise ValueError("начало ВКД должно лежать в периоде допустимых моментов начала")
        return self


class TrajectorySample(Frozen):
    t: datetime
    lat_deg: float
    lon_deg: float
    alt_km: float
    sunlit: bool
    mlat_deg: float | None
    in_saa: bool
    level: Level


class SaaPass(Frozen):
    """Проход через ЮАА как элемент картины с правилом, значением и провенансом, как интервалы линий."""

    start: datetime
    end: datetime
    min_b_nt: float
    min_at: datetime
    threshold_nt: float
    rule: str
    note: str
    provenance: list[Provenance]


class TrajectorySummary(Frozen):
    provider: str
    epoch: datetime
    is_reconstruction: bool
    provenance: Provenance
    age_at_query_hours: float | None
    samples: list[TrajectorySample]
    saa_passes: list[SaaPass] = Field(default_factory=list)


class TrajectoryQuery(Frozen):
    """Параметры `GET /trajectory`: интервал от 1 минуты до 48 часов, шаг сетки 1–10 минут."""

    start: datetime
    end: datetime
    step_minutes: int = Field(default=1, ge=1, le=10)
    mode: TrajectoryMode = "current"

    @field_validator("start", "end", mode="after")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def _span(self) -> "TrajectoryQuery":
        if not TRAJECTORY_MIN_SPAN <= self.end - self.start <= TRAJECTORY_MAX_SPAN:
            raise ValueError("интервал траектории должен быть от 1 минуты до 48 часов")
        return self


class TrajectoryResponse(TrajectorySummary):
    """Ответ `GET /trajectory`: сводка траектории без линий (level у всех точек unknown) и её ограничения."""

    limitations: list[str]


class OemInfo(Frozen):
    creation_date: datetime
    start_time: datetime
    stop_time: datetime
    provenance: Provenance


class OrbitLatest(Frozen):
    """Ответ `GET /orbit/latest`: OMM последней записи celestrak/gp и заголовок последнего OEM; без записи `None`."""

    omm: dict[str, Any] | None
    provenance: Provenance | None
    oem: OemInfo | None


class ConjunctionRow(Frozen):
    tca: datetime
    secondary_name: str
    secondary_catnr: int
    min_range_km: float
    rel_speed_kms: float
    max_probability: float
    level: Level
    coorbiting: bool


class ConjunctionsLatest(Frozen):
    """Ответ `GET /conjunctions/latest`: последний отчёт SOCRATES по МКС с уровнями строк, давностью и провенансом.

    Отчёт относится к моменту своего расчёта и следующим семи суткам; к историческим датам он не применяется.
    """

    available: bool
    data_as_of: datetime | None
    age_hours: float | None
    stale: bool
    max_report_age_hours: float
    interval_start: datetime | None
    interval_stop: datetime | None
    provenance: Provenance | None
    conjunctions: list[ConjunctionRow]
    coorbiting_rows: int
    limitations: list[str]


class ManualEntry(Frozen):
    name: str
    title: str


class RulesSnapshot(Frozen):
    """Ответ `GET /rules`: правила окон, параметры линии SEP и источники параметров по конфигам."""

    windows: dict[str, Any]
    sep: dict[str, Any]
    sources: dict[str, dict[str, str] | None]


class AccessGeometry(Frozen):
    """Страховка по геометрии (D61): минуты возможного доступа протонов при наибольшем Kp, информационно."""

    kp: float
    requested_minutes: float
    preferred_minutes: float | None
    safest: Window | None
    safest_minutes: float | None
    note: str


class AssessmentResult(Frozen):
    id: str
    created_at: datetime
    request: AssessmentRequest
    asof: datetime | None
    algorithm_version: str
    trajectory: TrajectorySummary
    lines: list[LineAssessment]
    requested_window: WindowScore
    windows: list[WindowScore]
    recommendation: Recommendation
    sources_used: list[Provenance]
    data_availability: dict[str, Availability]
    limitations: list[str]
    rules: list[str]
    verification: Verification | None = None
    geometry: AccessGeometry | None = None


class JobCreated(Frozen):
    job_id: str
    status: Literal["queued"] = "queued"


class JobStatus(Frozen):
    id: str
    kind: str
    params: dict[str, Any]
    status: str
    progress: dict[str, Any] | None
    error: str | None
    assessment_id: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_job(cls, job: Job) -> "JobStatus":
        return cls(
            id=job.id,
            kind=job.kind,
            params=job.params,
            status=job.status,
            progress=job.progress,
            error=job.error,
            assessment_id=job.assessment_id,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )


class SourceStateUpdate(Frozen):
    state: State
    reason: str = Field(min_length=1)


class SourceInfo(Frozen):
    name: str
    products: list[str]
    cadence_s: float | None
    archive_only: bool
    supports_archive: bool
    state: State
    reason: str | None
    changed_at: datetime | None
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    last_record_fetched_at: datetime | None
    records: int

    @classmethod
    def build(
        cls,
        name: str,
        *,
        products: tuple[str, ...],
        cadence_s: float | None,
        archive_only: bool = False,
        supports_archive: bool,
        state: SourceState,
        last_record_fetched_at: datetime | None,
        records: int,
    ) -> "SourceInfo":
        return cls(
            name=name,
            products=list(products),
            cadence_s=cadence_s,
            archive_only=archive_only,
            supports_archive=supports_archive,
            state=state.state,
            reason=state.reason,
            changed_at=state.changed_at,
            last_attempt_at=state.last_attempt_at,
            last_success_at=state.last_success_at,
            last_error=state.last_error,
            last_record_fetched_at=last_record_fetched_at,
            records=records,
        )
