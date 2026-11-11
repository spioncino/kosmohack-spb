import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evarisk.parsers.swpc_json import parse_integral_protons, parse_kp_1m, parse_kp_forecast

FIX = Path("tests/fixtures/swpc_live")


def test_integral_protons_fixture() -> None:
    rows = parse_integral_protons((FIX / "integral-protons-7-day.json").read_bytes())
    assert len(rows) == 16072
    first, last = rows[0], rows[-1]
    assert first.time == datetime(2026, 9, 11, 17, 25, tzinfo=UTC) and first.satellite == 18
    assert first.energy_mev == 1.0 and first.flux_pfu == 8.512261390686035
    assert last.time == datetime(2026, 9, 18, 17, 15, tzinfo=UTC) and last.energy_mev == 60.0
    assert last.flux_pfu == 0.19740355014801025
    assert {r.energy_mev for r in rows} == {1.0, 5.0, 10.0, 30.0, 50.0, 60.0, 100.0, 500.0}


def test_integral_protons_rejects_null_flux_and_odd_energy() -> None:
    good = {"time_tag": "2026-09-18T17:15:00Z", "satellite": 18, "flux": 0.2, "energy": ">=10 MeV"}
    assert parse_integral_protons(json.dumps([good]).encode())[0].flux_pfu == 0.2
    assert parse_integral_protons(b"[]") == []
    with pytest.raises(ValueError):
        parse_integral_protons(json.dumps([{**good, "flux": None}]).encode())
    with pytest.raises(ValueError):
        parse_integral_protons(json.dumps([{**good, "energy": "10 MeV"}]).encode())
    with pytest.raises(ValueError):
        parse_integral_protons(json.dumps([{"time_tag": "2026-09-18T17:15:00Z", "flux": 0.2}]).encode())
    with pytest.raises(ValueError):
        parse_integral_protons(b'{"time_tag": "x"}')


def test_kp_forecast_fixture() -> None:
    rows = parse_kp_forecast((FIX / "noaa-planetary-k-index-forecast.json").read_bytes())
    assert len(rows) == 81
    assert rows[0].start == datetime(2026, 9, 11, tzinfo=UTC) and rows[0].kp == 1.33 and rows[0].status == "observed"
    assert rows[-1].start == datetime(2026, 9, 21, tzinfo=UTC) and rows[-1].kp == 1.67
    assert rows[-1].status == "predicted" and rows[-1].noaa_scale is None
    by_start = {r.start: r for r in rows}
    assert by_start[datetime(2026, 9, 18, 15, tzinfo=UTC)].status == "estimated"
    assert by_start[datetime(2026, 9, 18, 12, tzinfo=UTC)].kp == 0.67
    assert [r.start for r in rows] == sorted(r.start for r in rows)


def test_kp_forecast_table_form_and_errors() -> None:
    table = [["time_tag", "kp", "observed", "noaa_scale"], ["2026-09-18T15:00:00", 2.0, "estimated", None]]
    rows = parse_kp_forecast(json.dumps(table).encode())
    assert rows == [
        type(rows[0])(start=datetime(2026, 9, 18, 15, tzinfo=UTC), kp=2.0, status="estimated", noaa_scale=None)
    ]
    with pytest.raises(ValueError):
        parse_kp_forecast(json.dumps([{"time_tag": "2026-09-18T15:00:00", "kp": 2.0, "observed": "guess"}]).encode())
    with pytest.raises(ValueError):
        parse_kp_forecast(json.dumps([["2026-09-18T15:00:00", 2.0, "predicted"]]).encode())
    with pytest.raises(ValueError):
        parse_kp_forecast(
            json.dumps(
                [{"time_tag": "2026-09-18T15:00:00", "kp": None, "observed": "predicted", "noaa_scale": None}]
            ).encode()
        )


def test_kp_1m_fixture() -> None:
    rows = parse_kp_1m((FIX / "planetary_k_index_1m.json").read_bytes())
    assert len(rows) == 358
    assert rows[0].time == datetime(2026, 9, 18, 11, 21, tzinfo=UTC) and rows[0].estimated_kp == 1.33
    assert rows[-1].time == datetime(2026, 9, 18, 17, 18, tzinfo=UTC) and rows[-1].estimated_kp == 0.33
    with pytest.raises(ValueError):
        parse_kp_1m(json.dumps([{"time_tag": "2026-09-18T11:21:00", "estimated_kp": "1.33"}]).encode())
