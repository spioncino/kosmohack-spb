from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from evarisk.api.schemas import AssessmentRequest, AssessmentResult, TrajectorySummary
from evarisk.types import (
    Availability,
    Confidence,
    Interval,
    Level,
    LineAssessment,
    Provenance,
    Recommendation,
    Window,
    WindowScore,
)

PROVENANCE = Provenance(
    raw_id="rawsep1",
    source="swpc_archive",
    product="3day_forecast",
    published_at=datetime(2024, 5, 10, 12, 30, tzinfo=UTC),
    published_at_kind="exact",
    data_version="v1",
    url="https://x.invalid/forecast",
)
TRAJECTORY_PROVENANCE = Provenance(
    raw_id="rawoem1",
    source="nasa_oem",
    product="oem",
    published_at=datetime(2024, 5, 10, 18, 51, tzinfo=UTC),
    published_at_kind="exact",
    data_version="oem 2024-05-10",
    url="https://x.invalid/oem",
)


def moment(hour: int, minute: int = 0, day: int = 11) -> datetime:
    return datetime(2024, 5, day, hour, minute, tzinfo=UTC)


def hours(none: float = 0.0, elevated: float = 0.0, high: float = 0.0, unknown: float = 0.0) -> dict[Level, float]:
    return {"none": none, "elevated": elevated, "high": high, "critical": 0.0, "unknown": unknown}


def interval(start: datetime, end: datetime, level: Level) -> Interval:
    return Interval(
        start=start,
        end=end,
        level=level,
        kind="computed",
        provenance=(PROVENANCE,),
        rule="правило линии",
        note="",
    )


def line(
    name: str = "sep_protons",
    intervals: tuple[Interval, ...] = (),
    availability: Availability = "available",
    grade: str = "medium",
) -> LineAssessment:
    return LineAssessment(
        line=name,
        mechanism="механизм",
        intervals=intervals,
        availability=availability,
        confidence=Confidence(grade=grade, depends_on="поток GOES, Kp и траектория доступны"),
        limitations=(),
    )


def window_score(
    start: datetime, end: datetime, levels: dict[Level, float], excluded: str | None = None
) -> WindowScore:
    return WindowScore(
        window=Window(start=start, end=end),
        per_line={"sep_protons": levels},
        completeness=1.0 if excluded is None else 0.0,
        excluded_reason=excluded,
    )


def make_result(
    *,
    mode: str = "replay",
    cutoff: datetime | None = moment(12, 30, day=10),
    eva_start: datetime = moment(4),
    duration_hours: float = 6.0,
    search_start: datetime = moment(0),
    search_end: datetime = moment(14),
    lines: tuple[LineAssessment, ...] = (),
    requested: WindowScore | None = None,
    windows: tuple[WindowScore, ...] = (),
    recommendation: Recommendation | None = None,
    limitations: tuple[str, ...] = (),
) -> AssessmentResult:
    """Небольшой сохранённый расчёт для проверок сводки: значения задаются явно, а не берутся из расчёта."""
    requested_score = requested or window_score(eva_start, eva_start + timedelta(hours=duration_hours), hours(none=6.0))
    rec = recommendation or Recommendation(
        preferred=requested_score.window, reason="причина", equivalents=(), insufficient=False, rules_applied=()
    )
    return AssessmentResult(
        id="briefcase01",
        created_at=datetime(2024, 5, 10, 12, 35, tzinfo=UTC),
        request=AssessmentRequest(
            mode=mode,
            cutoff=cutoff if mode == "replay" else None,
            eva_start=eva_start,
            duration_hours=duration_hours,
            search_start=search_start,
            search_end=search_end,
        ),
        asof=cutoff if mode == "replay" else (None if mode == "review" else datetime(2024, 5, 10, 12, 35, tzinfo=UTC)),
        algorithm_version="testver+cfg0000",
        trajectory=TrajectorySummary(
            provider="oem",
            epoch=moment(0, day=10),
            is_reconstruction=False,
            provenance=TRAJECTORY_PROVENANCE,
            age_at_query_hours=None,
            samples=[],
        ),
        lines=list(lines) or [line()],
        requested_window=requested_score,
        windows=list(windows) or [requested_score],
        recommendation=rec,
        sources_used=[PROVENANCE, TRAJECTORY_PROVENANCE],
        data_availability={a.line: a.availability for a in (lines or (line(),))},
        limitations=list(limitations),
        rules=[],
    )


@pytest.fixture
def build_result() -> Callable[..., AssessmentResult]:
    return make_result
