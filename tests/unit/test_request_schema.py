from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from evarisk.api.schemas import AssessmentRequest

BODY = {
    "mode": "replay",
    "eva_start": datetime(2024, 5, 11, 4, tzinfo=UTC),
    "duration_hours": 6,
    "search_start": datetime(2024, 5, 11, 0, tzinfo=UTC),
    "search_end": datetime(2024, 5, 12, 0, tzinfo=UTC),
}


def test_replay_cutoff_after_the_eva_start_is_rejected() -> None:
    # D80: отсечение 11 мая 05:00 позже начала ВКД 04:00, это прогноз задним числом.
    with pytest.raises(ValidationError, match="cutoff позже начала ВКД"):
        AssessmentRequest(**BODY, cutoff=datetime(2024, 5, 11, 5, tzinfo=UTC))


def test_replay_cutoff_at_or_before_the_eva_start_is_accepted() -> None:
    for cutoff in (datetime(2024, 5, 11, 4, tzinfo=UTC), datetime(2024, 5, 11, 3, tzinfo=UTC)):
        assert AssessmentRequest(**BODY, cutoff=cutoff).cutoff == cutoff
