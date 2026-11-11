import math
from pathlib import Path

import numpy as np
import pytest

from evarisk.risk.config import load_sep_config
from evarisk.risk.lines.dose_proxy import (
    cutoff_energy_mev,
    dose_rate_usv_h,
    load_dose_proxy_config,
    load_icrp116,
    load_pstar,
    shield_threshold_mev,
)
from evarisk.risk.lines.sep import cutoff_latitude_deg, rigidity_gv

CFG = load_dose_proxy_config()
SEP = load_sep_config()
ICRP_E, ICRP_H = load_icrp116(Path(CFG.icrp116_file), CFG.icrp116_geometry)
PSTAR_E, PSTAR_R = load_pstar(Path(CFG.pstar_file), CFG.range_column)
# 4π стерадиан и перевод pSv/s в мкЗв/ч: 3600 с/ч и 1e-6 мкЗв/pSv.
USV_H_PER_PSV_SR_S = 4.0 * math.pi * 3600.0 * 1e-6


def test_icrp116_iso_control_values() -> None:
    # ICRP 116, Annex A, протоны, ISO (docs/research/2026-09-18-literature-parameters.md).
    by_energy = dict(zip(ICRP_E.tolist(), ICRP_H.tolist(), strict=True))
    assert by_energy[10.0] == 45.8 and by_energy[50.0] == 451.0
    assert by_energy[100.0] == 1130.0 and by_energy[500.0] == 1180.0
    assert ICRP_E[0] == 1.0 and ICRP_E[-1] == 10000.0 and len(ICRP_E) == 33


def test_icrp116_geometry_columns() -> None:
    ap_e, ap_h = load_icrp116(Path(CFG.icrp116_file), "AP")
    assert dict(zip(ap_e.tolist(), ap_h.tolist(), strict=True))[10.0] == 54.9
    with pytest.raises(ValueError):
        load_icrp116(Path(CFG.icrp116_file), "XYZ")


def test_pstar_aluminum_control_values() -> None:
    # NIST PSTAR, Aluminum: CSDA 10 MeV 0.1705 г/см², 30 MeV 1.180 г/см².
    by_energy = dict(zip(PSTAR_E.tolist(), PSTAR_R.tolist(), strict=True))
    assert by_energy[10.0] == 0.1705 and by_energy[30.0] == 1.18
    projected_e, projected_r = load_pstar(Path(CFG.pstar_file), "projected")
    assert dict(zip(projected_e.tolist(), projected_r.tolist(), strict=True))[10.0] == 0.1697


def test_shield_threshold_from_range_table() -> None:
    # 0.18 г/см² лежит между 10 MeV (0.1705) и 12.5 MeV (0.2515); log-log интерполяция даёт 10.32 MeV.
    threshold = shield_threshold_mev(PSTAR_E, PSTAR_R, CFG.suit_shielding_g_cm2)
    expected = 10.0 * math.exp(math.log(0.18 / 0.1705) / math.log(0.2515 / 0.1705) * math.log(12.5 / 10.0))
    assert abs(threshold - expected) < 1e-9 and 10.0 < threshold < 15.0
    # Порог 4 мм Al (Leske 2001, с. 30019): 0.4 см × 2.699 г/см³ = 1.08 г/см², это 28–30 MeV, согласуется с 30 MeV.
    assert 28.0 < shield_threshold_mev(PSTAR_E, PSTAR_R, 1.08) < 30.0
    with pytest.raises(ValueError):
        shield_threshold_mev(PSTAR_E, PSTAR_R, 1e5)


def test_config_carries_sources_and_license() -> None:
    assert {"shielding", "icrp116", "icrp116_license", "pstar", "spectrum", "access", "formula"} <= set(CFG.sources)
    assert "MIT" in CFG.sources["icrp116_license"] and "openmc" in CFG.sources["icrp116"]
    assert "NIST" in CFG.sources["pstar"] and CFG.range_column == "csda"
    assert CFG.suit_shielding_g_cm2 == SEP.suit_shielding_g_cm2 == 0.18
    assert CFG.icrp116_geometry == "ISO"


def test_dose_rate_flat_spectrum_constant_coefficient() -> None:
    # φ = 1 protons/(cm² sr MeV s) на 10–100 MeV, h = 1000 pSv·cm²: ∫ = 90 000 pSv/(sr s).
    e = np.array([10.0, 100.0])
    flux = np.array([1.0, 1.0])
    table_e, table_h = np.array([1.0, 1e4]), np.array([1000.0, 1000.0])
    rate = dose_rate_usv_h(e, flux, 10.0, table_e, table_h)
    assert math.isclose(rate, 90_000.0 * USV_H_PER_PSV_SR_S, rel_tol=1e-9)
    # Узлы таблицы внутри сегмента не меняют интеграл постоянной функции.
    with_node = dose_rate_usv_h(e, flux, 10.0, np.array([1.0, 50.0, 1e4]), np.array([1000.0, 1000.0, 1000.0]))
    assert math.isclose(with_node, rate, rel_tol=1e-9)


def test_dose_rate_power_law_spectrum_and_lower_limit() -> None:
    # φ = 100/E² между 10 и 100 MeV, h = 1000: ∫_10^100 = 9, ∫_50^100 = 1 (в единицах 1000 pSv/(sr s)).
    e = np.array([10.0, 100.0])
    flux = np.array([1.0, 0.01])
    table_e, table_h = np.array([1.0, 1e4]), np.array([1000.0, 1000.0])
    assert math.isclose(dose_rate_usv_h(e, flux, 10.0, table_e, table_h), 9000.0 * USV_H_PER_PSV_SR_S, rel_tol=1e-9)
    assert math.isclose(dose_rate_usv_h(e, flux, 50.0, table_e, table_h), 1000.0 * USV_H_PER_PSV_SR_S, rel_tol=1e-9)
    assert dose_rate_usv_h(e, flux, 100.0, table_e, table_h) == 0.0
    assert dose_rate_usv_h(e, flux, 500.0, table_e, table_h) == 0.0


def test_dose_rate_with_energy_dependent_coefficient() -> None:
    # φ = 1 на 10–100 MeV, h = E pSv·cm²: ∫ E dE = (100² − 10²)/2 = 4950.
    e = np.array([10.0, 100.0])
    flux = np.array([1.0, 1.0])
    rate = dose_rate_usv_h(e, flux, 10.0, np.array([1.0, 1e4]), np.array([1.0, 1e4]))
    assert math.isclose(rate, 4950.0 * USV_H_PER_PSV_SR_S, rel_tol=1e-9)


def test_dose_rate_missing_channel_is_nan_not_zero() -> None:
    e = np.array([10.0, 30.0, 100.0])
    table_e, table_h = np.array([1.0, 1e4]), np.array([1000.0, 1000.0])
    assert math.isnan(dose_rate_usv_h(e, np.array([1.0, float("nan"), 0.1]), 10.0, table_e, table_h))
    # Отсутствие канала ниже нижнего предела не мешает интегралу выше него.
    rate = dose_rate_usv_h(e, np.array([float("nan"), 1.0, 1.0]), 30.0, table_e, table_h)
    assert math.isclose(rate, 70_000.0 * USV_H_PER_PSV_SR_S, rel_tol=1e-9)
    with pytest.raises(ValueError):
        dose_rate_usv_h(np.array([10.0]), np.array([1.0]), 10.0, table_e, table_h)


def test_cutoff_energy_inverts_cutoff_latitude() -> None:
    r30 = rigidity_gv(30.0, SEP.proton_rest_energy_mev)
    quiet = cutoff_latitude_deg(r30, SEP)
    assert abs(cutoff_energy_mev(quiet, SEP) - 30.0) < 1e-6
    storm = cutoff_latitude_deg(r30, SEP, kp=8.0)
    assert abs(cutoff_energy_mev(storm, SEP, kp=8.0) - 30.0) < 1e-6
    assert cutoff_energy_mev(-quiet, SEP) == cutoff_energy_mev(quiet, SEP)
    assert cutoff_energy_mev(90.0, SEP) == 0.0 and cutoff_energy_mev(85.0, SEP, kp=9.0) == 0.0
    assert cutoff_energy_mev(60.0, SEP) > cutoff_energy_mev(65.0, SEP) > cutoff_energy_mev(70.0, SEP)
    for mlat in (40.0, 55.0, 60.0, 62.0, 65.0, 68.0, 70.0, 75.0):
        for kp in (None, 3.0, 8.3):
            has_access = abs(mlat) >= cutoff_latitude_deg(r30, SEP, kp=kp)
            assert (cutoff_energy_mev(mlat, SEP, kp=kp) <= 30.0 + 1e-9) == has_access, (mlat, kp)
