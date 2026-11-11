from datetime import UTC, datetime
from pathlib import Path

import pytest

from evarisk.parsers.gfz_kp import parse_kp_json

FIX = Path("tests/fixtures/gfz/kp_2024-05-10_11.json")


def test_gannon_storm_kp() -> None:
    values = parse_kp_json(FIX.read_bytes())
    assert len(values) == 16
    assert values[0].start == datetime(2024, 5, 10, 0, 0, tzinfo=UTC) and values[0].kp == 2.667
    assert values[-1].start == datetime(2024, 5, 11, 21, 0, tzinfo=UTC) and values[-1].kp == 7.667
    peak = [v for v in values if v.kp == 9.0]
    assert [v.start for v in peak] == [datetime(2024, 5, 11, 0, 0, tzinfo=UTC), datetime(2024, 5, 11, 9, 0, tzinfo=UTC)]
    assert {v.status for v in values} == {"def"}
    assert [v.start for v in values] == sorted(v.start for v in values)


def test_empty_arrays_give_empty_list() -> None:
    assert parse_kp_json(b'{"Kp": [], "datetime": [], "status": []}') == []


def test_mismatched_lengths_raise() -> None:
    with pytest.raises(ValueError):
        parse_kp_json(b'{"Kp": [1.0, 2.0], "datetime": ["2024-05-10T00:00:00Z"], "status": ["def"]}')


def test_missing_status_raises() -> None:
    with pytest.raises(ValueError):
        parse_kp_json(b'{"Kp": [1.0], "datetime": ["2024-05-10T00:00:00Z"]}')
