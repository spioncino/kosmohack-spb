from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from evarisk.types import (
    Confidence,
    Interval,
    LineAssessment,
    Provenance,
    Window,
)

T0 = datetime(2024, 5, 10, 12, 0, tzinfo=UTC)
PROV = Provenance(
    raw_id="abc",
    source="swpc",
    product="3day",
    published_at=T0,
    published_at_kind="exact",
    data_version="issue 0030",
    url="https://example.invalid/x",
)


def test_interval_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError):
        Interval(start=datetime(2024, 5, 10), end=T0, level="high", kind="observation", provenance=(PROV,), rule="r")


def test_interval_requires_end_after_start() -> None:
    with pytest.raises(ValidationError):
        Interval(start=T0, end=T0, level="high", kind="observation", provenance=(PROV,), rule="r")


def test_window_duration() -> None:
    w = Window(start=T0, end=T0 + timedelta(hours=6))
    assert w.duration == timedelta(hours=6)


def test_missing_data_cannot_claim_level_none() -> None:
    quiet = Interval(start=T0, end=T0 + timedelta(hours=1), level="none", kind="computed", provenance=(PROV,), rule="r")
    with pytest.raises(ValidationError):
        LineAssessment(
            line="sep",
            mechanism="radiation",
            intervals=(quiet,),
            availability="missing",
            confidence=Confidence(grade="low", depends_on="x"),
            limitations=(),
        )


def test_models_are_frozen() -> None:
    w = Window(start=T0, end=T0 + timedelta(hours=1))
    with pytest.raises(ValidationError):
        w.start = T0  # type: ignore[misc]


def test_provenance_requires_version() -> None:
    with pytest.raises(ValidationError):
        Provenance(raw_id="a", source="s", product="p", published_at=T0, published_at_kind="exact", url="u")  # type: ignore[call-arg]
