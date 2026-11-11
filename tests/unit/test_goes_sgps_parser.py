from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from evarisk.parsers.goes_sgps import SgpsDay, integral_flux_pfu, read_sgps

FIX = Path("tests/fixtures/goes/sci_sgps-l2-avg5m_g18_d20240510_v3-0-2.nc")


def test_day_structure() -> None:
    day = read_sgps(FIX)
    assert day.satellite == 18
    assert len(day.times) == 288 and day.times[0] == datetime(2024, 5, 10, 0, 0, tzinfo=UTC)
    assert day.times[-1] == datetime(2024, 5, 10, 23, 55, tzinfo=UTC)
    assert all(t.tzinfo is not None for t in day.times)
    assert day.diff_flux.shape == (288, 2, 13) and day.lower_kev.shape == (2, 13) and day.upper_kev.shape == (2, 13)
    assert np.all(day.upper_kev > day.lower_kev)
    assert day.lower_kev[0, 0] == 1020.0 and day.upper_kev[0, 12] == 390000.0
    assert day.lower_kev[0, 5] == 11640.0 and day.lower_kev[1, 6] == 25900.0


def test_channel_p5_flux_at_official_peak_time() -> None:
    day = read_sgps(FIX)
    i = day.times.index(datetime(2024, 5, 10, 17, 45, tzinfo=UTC))
    assert day.diff_flux[i, 0, 5] == pytest.approx(1.0327769e-02, rel=1e-6)
    assert day.diff_flux[i, 1, 5] == pytest.approx(8.2999105e-03, rel=1e-6)


def test_sep_onset_on_2024_05_10() -> None:
    day = read_sgps(FIX)
    p10 = integral_flux_pfu(day, 10.0)
    assert p10.shape == (288,)
    assert p10[0] < 10.0
    assert p10[18 * 12] > 10.0
    assert np.nanmax(p10) > np.nanmax(p10[: 12 * 12])


def test_integral_flux_decreases_with_threshold() -> None:
    day = read_sgps(FIX)
    p10 = integral_flux_pfu(day, 10.0)
    p50 = integral_flux_pfu(day, 50.0)
    p100 = integral_flux_pfu(day, 100.0)
    assert np.all(p10 >= p50) and np.all(p50 >= p100)
    assert np.all(p100 >= 0.0)


def test_threshold_above_all_channels_gives_zero() -> None:
    day = read_sgps(FIX)
    assert np.all(integral_flux_pfu(day, 400.0) == 0.0)


def _two_sensor_day(flux: np.ndarray) -> SgpsDay:
    lower = np.array([[10000.0, 20000.0], [10000.0, 20000.0]])
    upper = np.array([[20000.0, 40000.0], [20000.0, 40000.0]])
    return SgpsDay(
        satellite=18,
        times=[datetime(2024, 5, 10, 0, 0, tzinfo=UTC)],
        diff_flux=flux,
        lower_kev=lower,
        upper_kev=upper,
    )


def test_flat_spectrum_integrates_to_width_times_flux() -> None:
    day = _two_sensor_day(np.full((1, 2, 2), 2.0))
    assert integral_flux_pfu(day, 10.0)[0] == pytest.approx(2.0 * (40000.0 - 10000.0))
    assert integral_flux_pfu(day, 25.0)[0] == pytest.approx(2.0 * (40000.0 - 25000.0))


def test_sensor_with_nan_is_ignored_and_all_nan_gives_nan() -> None:
    one_sensor = np.full((1, 2, 2), 2.0)
    one_sensor[0, 1, :] = np.nan
    assert integral_flux_pfu(_two_sensor_day(one_sensor), 10.0)[0] == pytest.approx(2.0 * 30000.0)
    assert np.isnan(integral_flux_pfu(_two_sensor_day(np.full((1, 2, 2), np.nan)), 10.0)[0])


def test_sensors_are_averaged() -> None:
    flux = np.full((1, 2, 2), 2.0)
    flux[0, 1, :] = 4.0
    assert integral_flux_pfu(_two_sensor_day(flux), 10.0)[0] == pytest.approx(3.0 * 30000.0)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises((ValueError, OSError)):
        read_sgps(tmp_path / "missing.nc")
