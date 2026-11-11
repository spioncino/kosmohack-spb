import math
from datetime import UTC, datetime, timedelta

import numpy as np

from evarisk.risk.config import SepConfig, load_sep_config
from evarisk.risk.lines.sep import (
    SepLine,
    _Observed,
    cutoff_latitude_deg,
    cutoff_rigidity_gv,
    env_level,
    quiet_cutoff_latitude_deg,
    rigidity_gv,
)
from evarisk.store.views_donki import SepEventKnown

CFG = load_sep_config()


def test_rigidity_of_protons() -> None:
    assert abs(rigidity_gv(10.0, CFG.proton_rest_energy_mev) - 0.1373) < 0.001
    assert abs(rigidity_gv(30.0, CFG.proton_rest_energy_mev) - 0.2392) < 0.001
    assert abs(rigidity_gv(100.0, CFG.proton_rest_energy_mev) - 0.4442) < 0.001


def test_quiet_cutoff_latitudes() -> None:
    # Фит SAMPEX (Ogliore et al. 2001): R = 15.062·cos⁴Λ − 0.363 ГВ, то есть Λ = arccos(((R + 0.363)/15.062)^0.25).
    # Для 30 MeV (R = 0.2392 ГВ): (0.6022/15.062)^0.25 = 0.4472, Λ = 63.44°. Для 100 MeV 61.2°, для 10 MeV 64.7°.
    lam100 = cutoff_latitude_deg(rigidity_gv(100.0, CFG.proton_rest_energy_mev), CFG)
    lam30 = cutoff_latitude_deg(rigidity_gv(30.0, CFG.proton_rest_energy_mev), CFG)
    lam10 = cutoff_latitude_deg(rigidity_gv(10.0, CFG.proton_rest_energy_mev), CFG)
    assert abs(lam30 - 63.44) < 0.05 and abs(lam100 - 61.24) < 0.05 and abs(lam10 - 64.73) < 0.05
    # Независимые измеренные оценки спокойной широты обрезания: 64.2° (Leske 2001) и 63.6° (Birch 2005), D62.
    assert abs(lam30 - 64.2) < 1.0 and abs(lam30 - 63.6) < 1.0
    # Дипольное приближение Штёрмера с C = 14.5 ГВ давало 69.0°: на 5° выше измерений.
    assert 69.0 - lam30 > 5.0


def test_cutoff_rigidity_is_the_inverse_of_the_cutoff_latitude() -> None:
    r = rigidity_gv(30.0, CFG.proton_rest_energy_mev)
    assert math.isclose(cutoff_rigidity_gv(quiet_cutoff_latitude_deg(r, CFG), CFG), r, rel_tol=1e-9)
    # Фит даёт нулевую жёсткость выше arccos((0.363/15.062)^0.25) = 66.8°: там проходят протоны любой энергии.
    assert cutoff_rigidity_gv(70.0, CFG) == 0.0 and cutoff_rigidity_gv(66.0, CFG) > 0.0


def test_high_and_critical_mean_exposure_only() -> None:
    # D63: без доступа обстановка S2 и S3 даёт elevated; при доступе решает спектр.
    line = SepLine(CFG)
    nan = float("nan")
    assert line._point_level(208.0, 40.0, 0.2, access=False) == "elevated"
    assert line._point_level(1030.0, 300.0, 5.0, access=False) == "elevated"
    assert line._point_level(12.0, 0.4, 0.01, access=False) == "elevated"
    assert line._point_level(0.3, 0.05, 0.01, access=True) == "none"
    assert line._point_level(12.0, 0.4, 0.01, access=True) == "elevated"
    assert line._point_level(12.0, 2.0, 0.2, access=True) == "high"
    assert line._point_level(208.0, 40.0, 1.5, access=True) == "critical"
    # Появление доступа не понижает уровень, пропавший канал p30 при обстановке S2 даёт high.
    assert line._point_level(208.0, nan, nan, access=True) == "high"
    assert line._point_level(nan, nan, nan, access=True) == "unknown"
    # Порог ESPE по потоку ≥100 MeV и порог ≥30 MeV при доступе не зависят от шкалы S: жёсткий спектр при p10 ниже
    # S1 всё равно даёт экспозицию (SRAG AES-CHP-SW-002, с. 9–10).
    assert line._point_level(6.0, 2.0, 1.2, access=True) == "critical"
    assert line._point_level(6.0, 1.5, 0.2, access=True) == "high"
    assert line._point_level(6.0, 1.5, 1.2, access=False) == "none"
    # Без измерения p10 при измеренном жёстком спектре на витке доступа уровень не теряется.
    assert line._point_level(nan, 2.0, 1.2, access=True) == "critical"


def test_storm_suppression_forms() -> None:
    r = rigidity_gv(30.0, CFG.proton_rest_energy_mev)
    quiet = cutoff_latitude_deg(r, CFG)
    assert math.isclose(quiet - cutoff_latitude_deg(r, CFG, kp=8.0), 0.92 * 8.0, abs_tol=1e-9)
    assert math.isclose(quiet - cutoff_latitude_deg(r, CFG, dst=-200.0), 200.0 * 0.05233, abs_tol=1e-9)
    assert cutoff_latitude_deg(r, CFG, kp=8.0, dst=-200.0) == cutoff_latitude_deg(r, CFG, dst=-200.0)
    bare = SepConfig(**{**CFG.model_dump(), "kp_suppression_deg_per_kp": None, "dst_suppression_deg_per_nt": None})
    assert cutoff_latitude_deg(r, bare, kp=8.0, dst=-200.0) == quiet
    assert cutoff_latitude_deg(r, CFG, dst=-5000.0) == 0.0


def test_env_levels() -> None:
    assert env_level(0.3, CFG) == "none" and env_level(10.0, CFG) == "elevated"
    assert env_level(208.0, CFG) == "high" and env_level(1030.0, CFG) == "critical"
    assert env_level(float("nan"), CFG) == "unknown"


def test_config_parameters_carry_sources() -> None:
    assert {"s_scale", "suit_threshold", "espe", "cutoff_fit", "dst_suppression", "kp_suppression"} <= set(CFG.sources)
    assert "Ogliore" in CFG.sources["cutoff_fit"] and "64.2" in CFG.sources["cutoff_fit"]
    assert {"forecast_horizon", "proton_outlook_threshold"} <= set(CFG.sources)
    assert CFG.kp_suppression_verified is False
    assert CFG.forecast_horizon_hours == 6.0 and CFG.proton_outlook_threshold_percent == 50.0
    assert (
        "наше правило" in CFG.sources["forecast_horizon"] and "наше правило" in CFG.sources["proton_outlook_threshold"]
    )


def test_hold_is_lifted_only_by_three_later_samples_below_the_event_threshold() -> None:
    # D60, D64: удержание снимают три последних измеренных отсчёта позже подтверждения ниже порога события
    # (правило трёх последовательных отсчётов GOES, SRAG AES-CHP-SW-002, с. 9–10).
    line = SepLine(CFG)
    confirmed = datetime(2024, 10, 9, 5, 30, tzinfo=UTC)
    event = SepEventKnown(10.0, 10.0, datetime(2024, 10, 9, 5, 5, tzinfo=UTC), confirmed, confirmed, "20241009-AL-002")
    later = confirmed + timedelta(hours=30)

    def observed(values: list[float], start: datetime = later) -> _Observed:
        series = np.asarray(values, dtype=np.float64)
        times = [start + timedelta(minutes=5 * i) for i in range(len(values))]
        return _Observed(times=times, p10=series, p30=series, p100=series)

    assert line._ended_by_flux(event, observed([25.0, 4.0, 3.0, 2.0])) is True
    # Один и два отсчёта ниже порога это провал канала или спад с возвратом, не конец события.
    assert line._ended_by_flux(event, observed([4.0])) is False
    assert line._ended_by_flux(event, observed([25.0, 25.0, 4.0, 3.0])) is False
    assert line._ended_by_flux(event, observed([4.0, 3.0, 25.0])) is False
    # Пропуски ряда в счёт не идут; отсчёты раньше подтверждения не учитываются.
    assert line._ended_by_flux(event, observed([4.0, float("nan"), 3.0, 2.0])) is True
    assert line._ended_by_flux(event, observed([4.0, 3.0, 2.0], start=confirmed - timedelta(hours=2))) is False
    assert line._ended_by_flux(event, observed([])) is False


def test_expired_event_lingers_for_96_hours_after_its_last_confirmation() -> None:
    # D64: событие с истёкшим удержанием держит уровень не ниже elevated до 96 ч от последнего подтверждения
    # (наибольшая измеренная длительность события ≥10 MeV 75.1 ч), позже оно не учитывается.
    line = SepLine(CFG)
    confirmed = datetime(2024, 5, 10, 14, 12, tzinfo=UTC)
    event = SepEventKnown(
        10.0, 10.0, datetime(2024, 5, 10, 13, 35, tzinfo=UTC), confirmed, confirmed, "20240510-AL-005"
    )
    assert CFG.donki_floor_hours == 96.0
    assert line._lingering((event,), confirmed + timedelta(hours=95, minutes=59)) == (event,)
    assert line._lingering((event,), confirmed + timedelta(hours=96, minutes=1)) == ()
    assert line._lingering((), confirmed) == ()
