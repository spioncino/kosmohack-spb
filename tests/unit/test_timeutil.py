from datetime import UTC, datetime, timedelta, timezone

import pytest

from evarisk.timeutil import ensure_utc, parse_utc, utc_now


def test_utc_now_is_aware_utc() -> None:
    now = utc_now()
    assert now.tzinfo is UTC


def test_ensure_utc_rejects_naive() -> None:
    with pytest.raises(ValueError):
        ensure_utc(datetime(2024, 5, 10, 12, 0))


def test_ensure_utc_converts_offset() -> None:
    msk = datetime(2024, 5, 10, 15, 0, tzinfo=timezone(timedelta(hours=3)))
    assert ensure_utc(msk) == datetime(2024, 5, 10, 12, 0, tzinfo=UTC)


def test_parse_utc_accepts_z_and_offset() -> None:
    assert parse_utc("2024-05-11T15:14Z") == datetime(2024, 5, 11, 15, 14, tzinfo=UTC)
    assert parse_utc("2024-05-11T18:14:00+03:00") == datetime(2024, 5, 11, 15, 14, tzinfo=UTC)


def test_parse_utc_naive_requires_flag() -> None:
    with pytest.raises(ValueError):
        parse_utc("2024-05-10T18:51:53.202")
    assert parse_utc("2024-05-10T18:51:53.202", assume_utc=True).tzinfo is UTC
