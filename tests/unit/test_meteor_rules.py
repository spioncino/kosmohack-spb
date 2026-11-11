from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import pytest

from evarisk.orbit.radiant import radiant_unit_gcrs
from evarisk.risk.lines.meteors import (
    MeteorLine,
    activity_fraction,
    interpolate_hourly,
    load_meteor_config,
    same_solar_longitude,
)

CFG = load_meteor_config()
BY_CODE = {s.code: s for s in CFG.showers}
ETA = BY_CODE["ETA"]
ZPE = BY_CODE["ZPE"]
ARI = BY_CODE["ARI"]
GEM = BY_CODE["GEM"]


def test_config_from_meo_tables() -> None:
    assert ETA.peak_utc == datetime(2024, 5, 5, 13, 53, tzinfo=UTC) and ETA.zhr_max == 161
    assert (ETA.radiant_ra_deg, ETA.radiant_dec_deg) == (338, -1) and ETA.speed_kms == 66 and ETA.highly_active
    assert ZPE.radiant_ra_deg is None and ZPE.radiant_dec_deg is None and not ZPE.highly_active
    assert ZPE.peak_utc == datetime(2024, 6, 2, 18, 25, tzinfo=UTC) and ZPE.zhr_max == 20
    assert (ARI.radiant_ra_deg, ARI.radiant_dec_deg, ARI.speed_kms) == (42, 24, 39) and ARI.highly_active
    assert (GEM.radiant_ra_deg, GEM.radiant_dec_deg, GEM.speed_kms, GEM.zhr_max) == (113, 32, 35, 140)
    assert {"meo", "suit_energy", "radiants", "shield", "imo", "iau_mdc", "thresholds"} <= set(CFG.sources)
    assert CFG.shield_radius_km == 6471.0 and 0.0 < CFG.elevated_factor < CFG.high_factor
    assert CFG.activity_calendar_year == 2024
    # Энергия частиц, способных пробить скафандр, по тексту прогноза MEO 2026: 6.7 Дж (0.04 см).
    assert CFG.suit_energy_joule == 6.7 and "EVA suit" in CFG.sources["suit_energy"]


def test_all_fifteen_showers_of_table_3() -> None:
    codes = [s.code for s in CFG.showers]
    expected = ["QUA", "ETA", "ZPE", "ARI", "SSG", "SDA", "CAP", "PER", "DSX", "DRA", "ORI", "STA", "NTA", "GEM", "URS"]
    assert codes == expected
    assert {s.code for s in CFG.showers if s.highly_active} == {"QUA", "ETA", "ARI", "SDA", "PER", "GEM"}
    for s in CFG.showers:
        assert s.start <= s.peak_utc.date() <= s.end, s.code
        assert s.source and s.peak_utc.tzinfo is not None
        assert (s.radiant_ra_deg is None) == (s.radiant_dec_deg is None), s.code
        assert s.highly_active == (s.radiant_ra_deg is not None), s.code
        assert "IMO" in s.source or "IAU MDC" in s.source or "допущение" in s.source, s.code


def test_activity_profile() -> None:
    assert ETA.start == date(2024, 4, 19) and ETA.end == date(2024, 5, 28)
    assert activity_fraction(ETA, ETA.peak_utc) == 1.0
    assert activity_fraction(ETA, datetime(2024, 6, 15, tzinfo=UTC)) == 0.0
    assert activity_fraction(ETA, datetime(2024, 4, 18, 23, 59, tzinfo=UTC)) == 0.0
    assert activity_fraction(ETA, datetime(2024, 4, 19, 0, 0, tzinfo=UTC)) == 0.0
    mid = activity_fraction(ETA, datetime(2024, 5, 15, tzinfo=UTC))
    assert 0.0 < mid < 1.0
    # Профиль линейный: посередине между максимумом (5 мая 13:53) и концом суток 28 мая доля 0.5.
    half = ETA.peak_utc + (datetime(2024, 5, 29, tzinfo=UTC) - ETA.peak_utc) / 2
    assert abs(activity_fraction(ETA, half) - 0.5) < 1e-9
    # Подъём от начала суток 19 апреля до максимума длится 16 сут 13 ч 53 мин; половина это 27 апреля 06:56:30.
    assert abs(activity_fraction(ETA, datetime(2024, 4, 27, 6, 56, 30, tzinfo=UTC)) - 0.5) < 1e-6


def test_activity_fraction_rejects_naive_time() -> None:
    with pytest.raises(ValueError):
        activity_fraction(ETA, datetime(2024, 5, 5, 13, 53))


def test_hourly_factor_is_interpolated_only_inside_the_series() -> None:
    times = [datetime(2024, 5, 11, h, tzinfo=UTC) for h in (11, 12, 14, 15)]
    values = np.asarray([0.04, 0.06, 0.10, 0.20])
    assert interpolate_hourly(times, values, datetime(2024, 5, 11, 12, tzinfo=UTC)) == 0.06
    assert interpolate_hourly(times, values, datetime(2024, 5, 11, 11, 30, tzinfo=UTC)) == pytest.approx(0.05)
    assert interpolate_hourly(times, values, datetime(2024, 5, 11, 14, 15, tzinfo=UTC)) == pytest.approx(0.125)
    # Разрыв ряда больше часа и моменты вне ряда без значения: минута без прогноза не спокойная.
    assert np.isnan(interpolate_hourly(times, values, datetime(2024, 5, 11, 13, tzinfo=UTC)))
    assert np.isnan(interpolate_hourly(times, values, datetime(2024, 5, 11, 10, 59, tzinfo=UTC)))
    assert np.isnan(interpolate_hourly(times, values, datetime(2024, 5, 11, 15, 1, tzinfo=UTC)))


def _station_along(ra_deg: float, dec_deg: float, sign: float) -> np.ndarray:
    return sign * 6771.0 * radiant_unit_gcrs(ra_deg, dec_deg).reshape(1, 3)


def test_earth_shields_only_when_all_active_radiants_are_hidden() -> None:
    line = MeteorLine(CFG)
    geminids = [datetime(2024, 12, 14, 2, tzinfo=UTC)]
    # 14 декабря активны только Геминиды (радиант RA 113°, Dec +32° из Table 2 MEO 2024): станция на стороне,
    # противоположной радианту, закрыта Землёй, станция на стороне радианта его видит.
    hidden, active = line._shielded(geminids, _station_along(113, 32, -1.0))
    assert active == ["GEM"] and hidden.tolist() == [True]
    visible, _ = line._shielded(geminids, _station_along(113, 32, 1.0))
    assert visible.tolist() == [False]
    # 8 июня активны Дневные Ариетиды с радиантом и Дневные дзета-Персеиды без радианта в прогнозе MEO: поток
    # без радианта считается видимым, экранирования нет даже на стороне, противоположной Ариетидам.
    june = [datetime(2024, 6, 8, 6, tzinfo=UTC)]
    shielded, active_june = line._shielded(june, _station_along(42, 24, -1.0))
    assert {"ARI", "ZPE"} <= set(active_june) and shielded.tolist() == [False]


def test_solar_longitude_shift_matches_imo_2027_calendar() -> None:
    # Даты максимумов 2027 года из IMO Meteor Shower Calendar 2027, Table 5: QUA Jan 04, ETA May 06, PER Aug 13,
    # GEM Dec 14. Перенос максимумов MEO 2024 на три года по солнечной долготе попадает в те же сутки с запасом 18 ч.
    for code, expected in (
        ("QUA", date(2027, 1, 4)),
        ("ETA", date(2027, 5, 6)),
        ("PER", date(2027, 8, 13)),
        ("GEM", date(2027, 12, 14)),
    ):
        shifted = same_solar_longitude(BY_CODE[code].peak_utc, 3)
        assert abs(shifted - datetime.combine(expected, time(12), tzinfo=UTC)) < timedelta(hours=18), code
    assert same_solar_longitude(GEM.peak_utc, 0) == GEM.peak_utc


def test_activity_carries_over_to_other_years_by_solar_longitude() -> None:
    assert activity_fraction(GEM, datetime(2026, 12, 14, 12, tzinfo=UTC)) > 0.9
    assert activity_fraction(GEM, datetime(2026, 6, 1, tzinfo=UTC)) == 0.0
    # Квадрантиды 2025 года начинаются 28 декабря 2024: период пересекает границу года.
    assert activity_fraction(BY_CODE["QUA"], datetime(2024, 12, 31, tzinfo=UTC)) > 0.0
    assert activity_fraction(BY_CODE["QUA"], datetime(2024, 12, 20, tzinfo=UTC)) == 0.0
