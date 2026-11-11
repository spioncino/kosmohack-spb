from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from evarisk.parsers.swpc_daily import parse_daypre, parse_geomag_forecast
from evarisk.parsers.swpc_text import KpCell

FIX = Path("tests/fixtures/swpc_archive")


def cell(cells: tuple[KpCell, ...], day: date, hour: int) -> KpCell:
    return next(c for c in cells if c.day == day and c.start_hour == hour)


def test_daypre_of_2024_05_10() -> None:
    d = parse_daypre((FIX / "20240510daypre.txt").read_text(encoding="utf-8"))
    assert d.issued_at == datetime(2024, 5, 10, 22, 0, tzinfo=UTC)
    assert d.days == (date(2024, 5, 11), date(2024, 5, 12), date(2024, 5, 13))
    assert len(d.mid_k) == 24 and len(d.high_k) == 24
    assert cell(d.mid_k, date(2024, 5, 11), 0).kp == 7.0 and cell(d.mid_k, date(2024, 5, 11), 3).kp == 6.0
    assert cell(d.mid_k, date(2024, 5, 12), 3).kp == 5.0 and cell(d.mid_k, date(2024, 5, 13), 21).kp == 3.0
    assert cell(d.high_k, date(2024, 5, 11), 6).kp == 7.0 and cell(d.high_k, date(2024, 5, 11), 9).kp == 7.0
    assert cell(d.high_k, date(2024, 5, 12), 21).kp == 2.0
    assert all(c.scale is None for c in d.mid_k + d.high_k)
    assert d.proton_probability == {date(2024, 5, 11): 99, date(2024, 5, 12): 75, date(2024, 5, 13): 60}


def test_daypre_without_proton_row_raises() -> None:
    text = (FIX / "20240510daypre.txt").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        parse_daypre(text.replace("Proton                   99            75            60\n", ""))


def test_daypre_with_missing_k_row_raises() -> None:
    text = (FIX / "20240510daypre.txt").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        parse_daypre(text.replace("Mid/09-12UT               6             3             3\n", ""))


def test_daypre_without_prediction_dates_raises() -> None:
    text = (FIX / "20240510daypre.txt").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        parse_daypre(text.replace(":Prediction_dates:", ":Dates:"))


def test_geomag_forecast_of_2024_05_09() -> None:
    g = parse_geomag_forecast((FIX / "20240509geomag_forecast.txt").read_text(encoding="utf-8"))
    assert g.issued_at == datetime(2024, 5, 9, 22, 10, tzinfo=UTC)
    assert g.days == (date(2024, 5, 10), date(2024, 5, 11), date(2024, 5, 12))
    assert g.observed_ap == (date(2024, 5, 8), 7) and g.estimated_ap == (date(2024, 5, 9), 11)
    assert len(g.kp) == 24 and all(c.scale is None for c in g.kp)
    assert cell(g.kp, date(2024, 5, 10), 0).kp == 3.33
    assert cell(g.kp, date(2024, 5, 11), 3).kp == 7.0 and cell(g.kp, date(2024, 5, 11), 6).kp == 8.33
    assert cell(g.kp, date(2024, 5, 11), 9).kp == 6.67 and cell(g.kp, date(2024, 5, 12), 21).kp == 3.67


def test_geomag_forecast_of_2024_05_08_and_10() -> None:
    g8 = parse_geomag_forecast((FIX / "20240508geomag_forecast.txt").read_text(encoding="utf-8"))
    assert g8.issued_at == datetime(2024, 5, 8, 22, 5, tzinfo=UTC)
    assert g8.observed_ap == (date(2024, 5, 7), 7) and g8.estimated_ap == (date(2024, 5, 8), 7)
    assert g8.days == (date(2024, 5, 9), date(2024, 5, 10), date(2024, 5, 11))
    assert cell(g8.kp, date(2024, 5, 11), 3).kp == 5.33 and cell(g8.kp, date(2024, 5, 11), 6).kp == 5.33
    assert cell(g8.kp, date(2024, 5, 11), 9).kp == 5.0
    g10 = parse_geomag_forecast((FIX / "20240510geomag_forecast.txt").read_text(encoding="utf-8"))
    assert g10.observed_ap == (date(2024, 5, 9), 7) and g10.estimated_ap == (date(2024, 5, 10), 97)
    assert cell(g10.kp, date(2024, 5, 11), 0).kp == 8.0 and cell(g10.kp, date(2024, 5, 13), 21).kp == 3.67


def test_geomag_forecast_ap_dates_cross_new_year() -> None:
    text = (FIX / "20240509geomag_forecast.txt").read_text(encoding="utf-8")
    for old, new in (
        (":Issued: 2024 May 09 2210 UTC", ":Issued: 2025 Jan 01 2210 UTC"),
        ("Observed Ap 08 May 007", "Observed Ap 31 Dec 007"),
        ("Estimated Ap 09 May 011", "Estimated Ap 01 Jan 011"),
        ("May 10    May 11    May 12", "Jan 02    Jan 03    Jan 04"),
    ):
        text = text.replace(old, new)
    g = parse_geomag_forecast(text)
    assert g.observed_ap == (date(2024, 12, 31), 7) and g.estimated_ap == (date(2025, 1, 1), 11)
    assert g.days == (date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 4))


def test_geomag_forecast_ap_on_leap_day_parses_in_the_leap_year() -> None:
    # Выпуск 1 марта 2024 ссылается на наблюдённый Ap за 29 февраля: в 2023 и 2025 годах такой даты нет.
    text = (FIX / "20240509geomag_forecast.txt").read_text(encoding="utf-8")
    for old, new in (
        (":Issued: 2024 May 09 2210 UTC", ":Issued: 2024 Mar 01 2210 UTC"),
        ("Observed Ap 08 May 007", "Observed Ap 29 Feb 007"),
        ("Estimated Ap 09 May 011", "Estimated Ap 01 Mar 011"),
        ("May 10    May 11    May 12", "Mar 02    Mar 03    Mar 04"),
    ):
        text = text.replace(old, new)
    g = parse_geomag_forecast(text)
    assert g.observed_ap == (date(2024, 2, 29), 7) and g.estimated_ap == (date(2024, 3, 1), 11)


def test_geomag_forecast_without_ap_lines_gives_none() -> None:
    text = (FIX / "20240509geomag_forecast.txt").read_text(encoding="utf-8")
    text = text.replace("Observed Ap 08 May 007\n", "").replace("Estimated Ap 09 May 011\n", "")
    g = parse_geomag_forecast(text)
    assert g.observed_ap is None and g.estimated_ap is None and len(g.kp) == 24


def test_geomag_forecast_without_kp_table_raises() -> None:
    text = (FIX / "20240509geomag_forecast.txt").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        parse_geomag_forecast(text.split("NOAA Kp index forecast", 1)[0])


def test_geomag_forecast_incomplete_table_raises() -> None:
    text = (FIX / "20240509geomag_forecast.txt").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        parse_geomag_forecast(text.replace("21-00UT        5.33      4.33      3.67      \n", ""))
