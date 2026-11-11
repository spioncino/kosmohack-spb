import csv
import math
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from evarisk.orbit.trajectory import Trajectory
from evarisk.risk.config import SepConfig
from evarisk.risk.lines.sep import (
    LIMIT_CME_KP,
    LIMIT_KP_AS_KNOWN,
    LIMIT_KP_FORECAST,
    LIMIT_KP_UNVERIFIED,
    LIMIT_NO_DST,
    LIMIT_NO_KP,
    LIMIT_NO_PROTONS,
    LIMIT_NO_SUPPRESSION,
    LOOKBACK,
    MIN_COVERAGE,
    NOTE_KP_ASSUMED,
    NOTE_KP_LAST_FORECAST,
    SAMPLE_TOLERANCE,
    STALE_AFTER,
    KpAt,
    KpSources,
    SepLine,
    cutoff_rigidity_gv,
    grid_step,
    nearest_index,
    rigidity_gv,
)
from evarisk.store.asof import AsOf
from evarisk.store.views import ProtonSeries, proton_series
from evarisk.types import Availability, Confidence, Frozen, Interval, Kind, Level, LineAssessment, Provenance, Window

LIMIT_ISO = (
    "коэффициенты ICRP 116 для изотропного поля: без ориентации тела, экранирования Землёй и конструкцией станции"
)
LIMIT_OUTSIDE_ACCESS = "вне минут доступа (порог 30 MeV) вклад протонов выше локального порога обрезания не учитывается"
LIMIT_NO_SPECTRUM = "нет спектра, только интегральные потоки: прокси дозы по ним не рассчитывается"
CONFIDENCE_NOTE = "прокси среды за номинальной защитой; без модели тела и ориентации"
_PSV_PER_S_TO_USV_PER_H = 3600.0 * 1e-6
_MINUTES_PER_HOUR = 60.0


class DoseProxyConfig(Frozen):
    suit_shielding_g_cm2: float
    icrp116_file: str
    icrp116_geometry: str
    pstar_file: str
    range_column: Literal["csda", "projected"]
    sources: dict[str, str]


def load_dose_proxy_config(path: Path = Path("config/dose_proxy.toml")) -> DoseProxyConfig:
    with path.open("rb") as fh:
        return DoseProxyConfig(**tomllib.load(fh))


def proxy_limitation(shielding_g_cm2: float) -> str:
    return f"прокси дозы среды за номинальной защитой скафандра {shielding_g_cm2:.2f} г/см², не доза человека (D34)"


def load_icrp116(path: Path, geometry: str) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Энергии (MeV) и коэффициенты флюенс → эффективная доза (pSv·cm²) из файла OpenMC для геометрии `geometry`."""
    lines = path.read_text(encoding="utf-8").splitlines()
    header = next((i for i, line in enumerate(lines) if line.startswith("Energy (MeV)")), None)
    if header is None:
        raise ValueError(f"{path}: нет строки заголовка Energy (MeV)")
    columns = lines[header].replace("Energy (MeV)", "Energy").split()
    if geometry not in columns:
        raise ValueError(f"{path}: нет геометрии {geometry!r} среди {columns[1:]}")
    col = columns.index(geometry)
    energies: list[float] = []
    values: list[float] = []
    for line in lines[header + 1 :]:
        parts = line.split()
        if len(parts) != len(columns):
            continue
        energies.append(float(parts[0]))
        values.append(float(parts[col]))
    e = np.asarray(energies, dtype=np.float64)
    h = np.asarray(values, dtype=np.float64)
    if e.size < 2 or not np.all(np.diff(e) > 0.0) or not np.all(h > 0.0):
        raise ValueError(f"{path}: таблица не возрастает по энергии или содержит неположительные коэффициенты")
    return e, h


def load_pstar(path: Path, column: Literal["csda", "projected"]) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Энергии (MeV) и пробег (г/см²) из CSV NIST PSTAR; строки с `#` это заголовок источника."""
    name = {"csda": "csda_range_g_cm2", "projected": "projected_range_g_cm2"}[column]
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(line for line in fh if not line.startswith("#")))
    if not rows or name not in rows[0]:
        raise ValueError(f"{path}: нет колонки {name}")
    e = np.asarray([float(r["energy_mev"]) for r in rows], dtype=np.float64)
    rng = np.asarray([float(r[name]) for r in rows], dtype=np.float64)
    if not np.all(np.diff(e) > 0.0) or not np.all(np.diff(rng) > 0.0):
        raise ValueError(f"{path}: энергия и пробег должны строго возрастать")
    return e, rng


def interp_loglog(x: NDArray[np.float64], xp: NDArray[np.float64], fp: NDArray[np.float64]) -> NDArray[np.float64]:
    """Линейная интерполяция в логарифмических осях без экстраполяции за пределы таблицы."""
    if np.any(x < xp[0]) or np.any(x > xp[-1]):
        raise ValueError(f"энергия вне таблицы {xp[0]}–{xp[-1]} MeV")
    return np.asarray(np.exp(np.interp(np.log(x), np.log(xp), np.log(fp))), dtype=np.float64)


def shield_threshold_mev(
    energies_mev: NDArray[np.float64], ranges_g_cm2: NDArray[np.float64], shielding_g_cm2: float
) -> float:
    """Энергия протона, чей пробег равен толщине защиты: log-log интерполяция таблицы пробегов."""
    value = interp_loglog(np.asarray([shielding_g_cm2], dtype=np.float64), ranges_g_cm2, energies_mev)
    return float(value[0])


def kinetic_energy_mev(rigidity_gv_value: float, rest_mev: float) -> float:
    p_mev = rigidity_gv_value * 1000.0
    return math.sqrt(p_mev**2 + rest_mev**2) - rest_mev


def cutoff_energy_mev(mlat_deg: float, cfg: SepConfig, *, kp: float | None = None) -> float:
    """Наименьшая энергия протона с доступом в точке: обращение `cutoff_latitude_deg` в Kp-форме (D36).

    Спокойная широта обрезания для жёсткости R это arccos(((R+R0)/C)^0.25) (D62); буря снижает её на k·Kp.
    Доступ в точке есть при |mlat| >= Λc(R), то есть при R >= C·cos⁴(|mlat| + k·Kp) − R0; при |mlat| + k·Kp >= 90°
    и там, где фит даёт жёсткость ниже нуля, доступ есть для любой энергии и порог равен нулю.
    """
    shift = 0.0 if kp is None or cfg.kp_suppression_deg_per_kp is None else kp * cfg.kp_suppression_deg_per_kp
    lam = abs(mlat_deg) + shift
    if lam >= 90.0:
        return 0.0
    rigidity = cutoff_rigidity_gv(lam, cfg)
    return kinetic_energy_mev(rigidity, cfg.proton_rest_energy_mev)


def _flux_between(e1: float, e2: float, j1: float, j2: float, x: float) -> float:
    """Поток в точке `x` сегмента [e1, e2]: степенной закон при положительных концах, иначе линейный (D35)."""
    if j1 > 0.0 and j2 > 0.0:
        gamma = math.log(j2 / j1) / math.log(e2 / e1)
        return j1 * math.pow(x / e1, gamma)
    return j1 + (j2 - j1) * (x - e1) / (e2 - e1)


def _power_integral(a: float, b: float, pa: float, pb: float) -> float:
    """Интеграл произведения поток·коэффициент на [a, b] по двум точкам: степенной закон или трапеция при нулях."""
    if pa > 0.0 and pb > 0.0:
        k = math.log(pb / pa) / math.log(b / a)
        if math.isclose(k, -1.0):
            return pa * a * math.log(b / a)
        return pa * a / (k + 1.0) * (math.pow(b / a, k + 1.0) - 1.0)
    return 0.5 * (pa + pb) * (b - a)


def dose_rate_usv_h(
    energies_mev: NDArray[np.float64],
    diff_flux: NDArray[np.float64],
    e_min_mev: float,
    icrp_e: NDArray[np.float64],
    icrp_h: NDArray[np.float64],
) -> float:
    """Мощность дозы прокси в мкЗв/ч: 4π · ∫ φ(E)·h(E) dE от `e_min_mev` до последней эффективной энергии.

    `diff_flux` в protons/(cm² sr MeV s) на эффективных энергиях каналов, `icrp_h` в pSv·cm². Спектр между
    эффективными энергиями кусочно-степенной, коэффициент интерполируется в log-log; на каждом подотрезке
    между узлами обеих сеток произведение это степенной закон и интегрируется аналитически. `NaN` при
    отсутствии отсчёта в любом канале выше нижнего предела.
    """
    if energies_mev.size != diff_flux.size or energies_mev.size < 2:
        raise ValueError("спектр должен содержать не меньше двух каналов на тех же энергиях")
    e_max = float(energies_mev[-1])
    if e_min_mev >= e_max:
        return 0.0
    total = 0.0
    for i in range(energies_mev.size - 1):
        e1, e2 = float(energies_mev[i]), float(energies_mev[i + 1])
        if e2 <= e_min_mev:
            continue
        j1, j2 = float(diff_flux[i]), float(diff_flux[i + 1])
        if math.isnan(j1) or math.isnan(j2):
            return float("nan")
        lower = max(e1, e_min_mev)
        inner = icrp_e[(icrp_e > lower) & (icrp_e < e2)]
        nodes = np.concatenate(([lower], inner, [e2]))
        h = interp_loglog(nodes, icrp_e, icrp_h)
        phi = [_flux_between(e1, e2, j1, j2, float(x)) for x in nodes]
        for k in range(nodes.size - 1):
            a, b = float(nodes[k]), float(nodes[k + 1])
            total += _power_integral(a, b, phi[k] * float(h[k]), phi[k + 1] * float(h[k + 1]))
    return 4.0 * math.pi * total * _PSV_PER_S_TO_USV_PER_H


@dataclass(frozen=True)
class _DosePoint:
    """Точка траектории: мощность дозы (`NaN` без отсчёта спектра), доступ и Kp по правилам линии SEP."""

    rate: float
    access: bool
    kp: KpAt
    kind: Kind
    kp_note: str


class DoseProxyLine:
    name = "dose_proxy"
    mechanism = "radiation_dose_proxy"

    def __init__(self, cfg: DoseProxyConfig, sep_cfg: SepConfig) -> None:
        self.cfg = cfg
        self.sep_cfg = sep_cfg
        self._sep = SepLine(sep_cfg)
        self._icrp_e, self._icrp_h = load_icrp116(Path(cfg.icrp116_file), cfg.icrp116_geometry)
        pstar_e, pstar_r = load_pstar(Path(cfg.pstar_file), cfg.range_column)
        self.shield_threshold_mev = shield_threshold_mev(pstar_e, pstar_r, cfg.suit_shielding_g_cm2)
        self._r_suit = rigidity_gv(sep_cfg.suit_threshold_mev, sep_cfg.proton_rest_energy_mev)

    def _rule(self, e_last_mev: float) -> str:
        cfg = self.sep_cfg
        kp_part = "" if cfg.kp_suppression_deg_per_kp is None else f" минус {cfg.kp_suppression_deg_per_kp}°·Kp"
        threshold = self.shield_threshold_mev
        shielding = self.cfg.suit_shielding_g_cm2
        return (
            f"мощность дозы = 4π·∫φ(E)·h_ISO(E)dE от max({threshold:.1f} MeV, порог обрезания в точке) "
            f"до {e_last_mev:.0f} MeV по кусочно-степенному спектру GOES SGPS и ICRP 116 "
            f"({self.cfg.icrp116_geometry}); {threshold:.1f} MeV это пробег {shielding:.2f} г/см² Al "
            f"по NIST PSTAR ({self.cfg.range_column}); учитывается на минутах доступа "
            f"|mlat| >= arccos(((R+{cfg.cutoff_offset_gv})/{cfg.cutoff_constant_gv})^0.25) "
            f"при R({cfg.suit_threshold_mev:.0f} MeV)"
            f"={self._r_suit:.3f} GV{kp_part}; elevated при ненулевой дозе"
        )

    def _missing(self, limitations: list[str], reason: str) -> LineAssessment:
        return LineAssessment(
            line=self.name,
            mechanism=self.mechanism,
            intervals=(),
            availability="missing",
            confidence=Confidence(grade="low", depends_on=CONFIDENCE_NOTE),
            limitations=tuple(dict.fromkeys([*limitations, reason])),
            metrics={"shield_threshold_mev": self.shield_threshold_mev},
        )

    def _point(self, t: datetime, mlat: float, protons: ProtonSeries, kp_sources: KpSources) -> _DosePoint:
        """Мощность дозы в точке; доступ и Kp берутся по правилам линии SEP, при неизвестном Kp это Kp 9 (D54)."""
        kp = kp_sources.at(t)
        kp_access = self._sep.access_kp(kp)
        has_access = self._sep.has_access(mlat, kp_access)
        row = nearest_index(protons.times, t, SAMPLE_TOLERANCE)
        if row is None:
            rate = float("nan")
        elif not has_access:
            rate = 0.0
        else:
            e_min = max(self.shield_threshold_mev, cutoff_energy_mev(mlat, self.sep_cfg, kp=kp_access))
            rate = dose_rate_usv_h(protons.energies_mev, protons.diff_flux[row], e_min, self._icrp_e, self._icrp_h)
        if kp.value is None:
            kp_note = f"Kp {NOTE_KP_ASSUMED.format(kp=self.sep_cfg.kp_assumed_when_missing)}"
        elif kp.last_forecast:
            kp_note = f"Kp: {NOTE_KP_LAST_FORECAST} (D54)"
        else:
            kp_note = ""
        kind: Kind = "external_forecast" if kp.forecast else "computed"
        return _DosePoint(rate, has_access, kp, kind, kp_note)

    def _kp_limitations(self, points: list[_DosePoint], kp_sources: KpSources) -> list[str]:
        """Примечания о Kp в тех же условиях, что у линии SEP."""
        cfg = self.sep_cfg
        out: list[str] = []
        if cfg.kp_suppression_deg_per_kp is None:
            out.append(LIMIT_NO_SUPPRESSION)
        else:
            if any(p.kp.value is not None for p in points):
                out.append(LIMIT_NO_DST)
                if not cfg.kp_suppression_verified:
                    out.append(LIMIT_KP_UNVERIFIED)
            if any(p.kp.value is None for p in points):
                out.append(LIMIT_NO_KP.format(kp=cfg.kp_assumed_when_missing))
        if kp_sources.t_cut is not None:
            if any(p.kp.value is not None and not p.kp.forecast for p in points):
                out.append(LIMIT_KP_AS_KNOWN)
            if kp_sources.forecast is not None and any(p.kp.forecast for p in points):
                out.append(LIMIT_KP_FORECAST.format(version=kp_sources.forecast.data_version))
            if any(p.kp.cme_seen for p in points):
                out.append(LIMIT_CME_KP)
        return out

    def assess(self, window: Window, trajectory: Trajectory, asof: AsOf) -> LineAssessment:
        limitations: list[str] = [
            proxy_limitation(self.cfg.suit_shielding_g_cm2),
            LIMIT_ISO,
            LIMIT_OUTSIDE_ACCESS,
            *trajectory.limitations,
        ]
        protons: ProtonSeries | None = proton_series(asof, window.start - LOOKBACK, window.end)
        if protons is None:
            return self._missing(limitations, LIMIT_NO_PROTONS)
        limitations.append(protons.note)
        if protons.energies_mev.size == 0:
            return self._missing(limitations, LIMIT_NO_SPECTRUM)
        e_last = float(protons.energies_mev[-1])
        limitations.append(f"спектр выше {e_last:.0f} MeV (последняя эффективная энергия каналов SGPS) не учтён")
        kp_sources = self._sep.kp_sources(window, asof)
        step = grid_step(trajectory.times)
        step_minutes = step.total_seconds() / _MINUTES_PER_HOUR
        in_window = [i for i, t in enumerate(trajectory.times) if window.start <= t < window.end]
        points = [
            self._point(trajectory.times[i], float(trajectory.mlat_deg[i]), protons, kp_sources) for i in in_window
        ]
        limitations.extend(self._kp_limitations(points, kp_sources))
        kp_used = [
            (kp_sources.known, any(p.kp.value is not None and not p.kp.forecast for p in points)),
            (kp_sources.forecast, any(p.kp.forecast and not p.kp.last_forecast for p in points)),
            (kp_sources.elapsed_forecast, any(p.kp.last_forecast for p in points)),
            (kp_sources.cme, any(p.kp.cme_seen for p in points)),
        ]
        provenance_by_id: dict[str, Provenance] = {}
        for prov in (
            *protons.provenance,
            *(prov for series, used in kp_used if series is not None and used for prov in series.provenance),
            trajectory.provenance,
        ):
            provenance_by_id.setdefault(prov.raw_id, prov)
        provenance = tuple(provenance_by_id.values())
        rates = [p.rate for p in points]
        levels: list[Level] = [
            "unknown" if math.isnan(p.rate) else ("elevated" if p.access and p.rate > 0.0 else "none") for p in points
        ]
        covered = sum(1 for r in rates if not math.isnan(r))
        coverage = covered / max(1, len(in_window))
        availability: Availability = "available" if coverage >= MIN_COVERAGE else "missing"
        if availability == "available" and protons.times[-1] < window.start - STALE_AFTER:
            availability = "stale"
        rule = self._rule(e_last)
        intervals: list[Interval] = []
        run_start = 0
        for j in range(1, len(in_window) + 1):
            head = points[run_start]
            if j < len(in_window) and (levels[j], points[j].kind, points[j].kp_note) == (
                levels[run_start],
                head.kind,
                head.kp_note,
            ):
                continue
            t0 = trajectory.times[in_window[run_start]]
            t1 = min(trajectory.times[in_window[j - 1]] + step, window.end)
            level = levels[run_start]
            if availability != "available" and level == "none":
                level = "unknown"
            note = self._note(levels[run_start], rates[run_start:j])
            intervals.append(
                Interval(
                    start=t0,
                    end=t1,
                    level=level,
                    kind=head.kind,
                    provenance=provenance,
                    rule=rule,
                    note=f"{note}; {head.kp_note}" if head.kp_note else note,
                )
            )
            run_start = j
        finite = [r for r in rates if not math.isnan(r)]
        metrics: dict[str, float] = {
            "shield_threshold_mev": self.shield_threshold_mev,
            "access_minutes": sum(1 for p in points if p.access) * step_minutes,
            "coverage": coverage,
        }
        # Доза за окно по покрытию ниже MIN_COVERAGE была бы суммой по малой части минут: числа нет, а не ноль.
        if finite and availability != "missing":
            metrics["dose_proxy_usv_window"] = sum(finite) * step_minutes / _MINUTES_PER_HOUR
            metrics["dose_rate_max_usv_h"] = max(finite)
        return LineAssessment(
            line=self.name,
            mechanism=self.mechanism,
            intervals=tuple(intervals),
            availability=availability,
            confidence=Confidence(grade="low", depends_on=CONFIDENCE_NOTE),
            limitations=tuple(dict.fromkeys(limitations)),
            metrics=metrics,
        )

    @staticmethod
    def _note(level: Level, rates: list[float]) -> str:
        if level == "unknown":
            return "нет отсчёта спектра протонов"
        if level == "elevated":
            return f"мощность дозы прокси до {max(rates):.3g} мкЗв/ч"
        return "вне доступа частиц или нулевая доза прокси"
