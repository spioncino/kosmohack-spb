from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from evarisk.parsers.swpc_text import issued_at, parse_three_day_forecast

FIX = Path("tests/fixtures/swpc_archive")


def test_issued_at() -> None:
    text = (FIX / "20240510geomag_forecast.txt").read_text(encoding="utf-8")
    assert issued_at(text) == datetime(2024, 5, 10, 22, 5, tzinfo=UTC)


def test_issued_at_missing_raises() -> None:
    with pytest.raises(ValueError):
        issued_at(":Product: x.txt\n# no issue line\n")


def test_issued_at_dayind_ut_suffix() -> None:
    text = (FIX / "20240510dayind.txt").read_text(encoding="utf-8")
    assert issued_at(text) == datetime(2024, 5, 11, 18, 16, tzinfo=UTC)


def test_three_day_forecast_of_2024_05_10() -> None:
    f = parse_three_day_forecast((FIX / "202405100030three_day_forecast.txt").read_text(encoding="utf-8"))
    assert f.issued_at == datetime(2024, 5, 10, 0, 30, tzinfo=UTC)
    assert f.days == (date(2024, 5, 10), date(2024, 5, 11), date(2024, 5, 12))
    assert len(f.kp) == 24
    cell = next(c for c in f.kp if c.day == date(2024, 5, 11) and c.start_hour == 6)
    assert cell.kp == 8.33 and cell.scale == "G4"
    quiet = next(c for c in f.kp if c.day == date(2024, 5, 10) and c.start_hour == 0)
    assert quiet.kp == 3.33 and quiet.scale is None
    last = next(c for c in f.kp if c.day == date(2024, 5, 12) and c.start_hour == 21)
    assert last.kp == 3.67 and last.scale is None
    assert f.max_expected_kp == 8.33 and f.observed_max_kp_24h == 2.0
    assert f.s1_probability == {d: 50 for d in f.days}
    assert f.r1_r2_probability[date(2024, 5, 10)] == 95 and f.r3_probability[date(2024, 5, 12)] == 60


def test_three_day_forecast_of_2024_05_10_1230_has_elapsed_cells() -> None:
    f = parse_three_day_forecast((FIX / "202405101230three_day_forecast.txt").read_text(encoding="utf-8"))
    assert f.issued_at == datetime(2024, 5, 10, 12, 30, tzinfo=UTC)
    assert f.observed_max_kp_24h == 3.0 and f.max_expected_kp == 8.33
    elapsed = [c.kp for c in f.kp if c.day == date(2024, 5, 10) and c.start_hour < 12]
    assert elapsed == [3.0, 3.0, 2.67, 2.33]
    cell = next(c for c in f.kp if c.day == date(2024, 5, 11) and c.start_hour == 6)
    assert cell.kp == 8.33 and cell.scale == "G4"
    assert f.s1_probability == {d: 55 for d in f.days}


def test_three_day_forecast_live_product_of_2026_09_18() -> None:
    f = parse_three_day_forecast(Path("tests/fixtures/swpc_live/3-day-forecast.txt").read_text(encoding="utf-8"))
    assert f.issued_at == datetime(2026, 9, 18, 12, 30, tzinfo=UTC)
    assert f.days == (date(2026, 9, 18), date(2026, 9, 19), date(2026, 9, 20))
    assert len(f.kp) == 24 and all(c.scale is None for c in f.kp)
    cell = next(c for c in f.kp if c.day == date(2026, 9, 18) and c.start_hour == 21)
    assert cell.kp == 3.0
    assert f.max_expected_kp == 3.0 and f.observed_max_kp_24h == 3.0
    assert f.s1_probability == {d: 1 for d in f.days}
    assert f.r1_r2_probability == {d: 1 for d in f.days} and f.r3_probability == {d: 1 for d in f.days}


def test_three_day_forecast_days_cross_new_year() -> None:
    text = (FIX / "202405100030three_day_forecast.txt").read_text(encoding="utf-8")
    for old, new in (("May 10", "Dec 31"), ("May 11", "Jan 01"), ("May 12", "Jan 02")):
        text = text.replace(old, new)
    f = parse_three_day_forecast(text)
    assert f.issued_at == datetime(2024, 12, 31, 0, 30, tzinfo=UTC)
    assert f.days == (date(2024, 12, 31), date(2025, 1, 1), date(2025, 1, 2))


def test_three_day_forecast_incomplete_table_raises() -> None:
    text = (FIX / "202405100030three_day_forecast.txt").read_text(encoding="utf-8")
    broken = text.replace("21-00UT       5.33 (G1)    4.33         3.67     \n", "")
    with pytest.raises(ValueError):
        parse_three_day_forecast(broken)


def test_three_day_forecast_without_probabilities_raises() -> None:
    text = (FIX / "202405100030three_day_forecast.txt").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        parse_three_day_forecast(text.split("B. NOAA Solar Radiation", 1)[0])


def test_three_day_forecast_archive_2023_header_without_leading_colon() -> None:
    f = parse_three_day_forecast((FIX / "202307170030three_day_forecast.txt").read_text(encoding="utf-8"))
    assert f.issued_at == datetime(2023, 7, 17, 0, 30, tzinfo=UTC)
    assert f.days == (date(2023, 7, 17), date(2023, 7, 18), date(2023, 7, 19))
    assert len(f.kp) == 24
    storm = next(c for c in f.kp if c.day == date(2023, 7, 18) and c.start_hour == 0)
    assert storm.kp == 4.67 and storm.scale == "G1"
    quiet = next(c for c in f.kp if c.day == date(2023, 7, 19) and c.start_hour == 0)
    assert quiet.kp == 2.0 and quiet.scale is None
    assert f.max_expected_kp == 4.67 and f.observed_max_kp_24h == 5.0
    assert f.s1_probability == {date(2023, 7, 17): 25, date(2023, 7, 18): 20, date(2023, 7, 19): 10}
    assert f.r1_r2_probability == {date(2023, 7, 17): 60, date(2023, 7, 18): 60, date(2023, 7, 19): 50}


def test_three_day_forecast_archive_2022_has_integer_kp_cells() -> None:
    f = parse_three_day_forecast((FIX / "202206110030three_day_forecast.txt").read_text(encoding="utf-8"))
    assert f.issued_at == datetime(2022, 6, 11, 0, 30, tzinfo=UTC)
    assert f.days == (date(2022, 6, 11), date(2022, 6, 12), date(2022, 6, 13))
    assert len(f.kp) == 24 and all(c.scale is None for c in f.kp)
    first = next(c for c in f.kp if c.day == date(2022, 6, 11) and c.start_hour == 0)
    assert first.kp == 2.0
    lowest = next(c for c in f.kp if c.day == date(2022, 6, 12) and c.start_hour == 3)
    assert lowest.kp == 1.0
    last = next(c for c in f.kp if c.day == date(2022, 6, 13) and c.start_hour == 21)
    assert last.kp == 1.0
    assert f.max_expected_kp == 2.0 and f.observed_max_kp_24h == 2.0
    assert f.s1_probability == {d: 1 for d in f.days}
    assert f.r1_r2_probability == {d: 10 for d in f.days} and f.r3_probability == {d: 1 for d in f.days}
