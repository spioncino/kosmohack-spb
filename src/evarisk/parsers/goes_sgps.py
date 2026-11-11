from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np
from numpy.typing import NDArray

_EPOCH = datetime(2000, 1, 1, 12, 0, tzinfo=UTC)
_TIME_UNITS_PREFIX = "seconds since 2000-01-01 12:00:00"
_PLATFORM = re.compile(r"^g(\d+)$")
_FLUX = "AvgDiffProtonFlux"
_LOWER = "DiffProtonLowerEnergy"
_UPPER = "DiffProtonUpperEnergy"


@dataclass(frozen=True)
class SgpsDay:
    satellite: int
    times: list[datetime]
    diff_flux: NDArray[np.float64]
    lower_kev: NDArray[np.float64]
    upper_kev: NDArray[np.float64]


def _filled(var: netCDF4.Variable[Any]) -> NDArray[np.float64]:
    return np.asarray(np.ma.filled(var[:].astype(np.float64), np.nan), dtype=np.float64)


def _satellite(ds: netCDF4.Dataset) -> int:
    platform = str(getattr(ds, "platform", ""))
    m = _PLATFORM.match(platform)
    if m is None:
        raise ValueError(f"атрибут platform не вида gNN: {platform!r}")
    return int(m.group(1))


def read_sgps(path: Path) -> SgpsDay:
    with netCDF4.Dataset(path) as ds:
        missing = [v for v in ("time", _FLUX, _LOWER, _UPPER) if v not in ds.variables]
        if missing:
            raise ValueError(f"в файле SGPS нет переменных {missing}")
        units = str(getattr(ds.variables["time"], "units", ""))
        if not units.startswith(_TIME_UNITS_PREFIX):
            raise ValueError(f"неожиданные единицы времени: {units!r}")
        flux_units = str(getattr(ds.variables[_FLUX], "units", ""))
        if "keV" not in flux_units:
            raise ValueError(f"неожиданные единицы потока: {flux_units!r}")
        seconds = np.asarray(ds.variables["time"][:], dtype=np.float64)
        flux = _filled(ds.variables[_FLUX])
        lower = _filled(ds.variables[_LOWER])
        upper = _filled(ds.variables[_UPPER])
        satellite = _satellite(ds)
    if flux.ndim != 3 or lower.shape != flux.shape[1:] or upper.shape != flux.shape[1:]:
        raise ValueError(f"формы SGPS не согласованы: поток {flux.shape}, границы {lower.shape}, {upper.shape}")
    if flux.shape[0] != seconds.shape[0]:
        raise ValueError(f"поток {flux.shape[0]} отсчётов, время {seconds.shape[0]}")
    if np.isnan(lower).any() or np.isnan(upper).any() or not np.all(upper > lower):
        raise ValueError("границы каналов SGPS не заданы или не возрастают")
    return SgpsDay(
        satellite=satellite,
        times=[_EPOCH + timedelta(seconds=float(s)) for s in seconds],
        diff_flux=flux,
        lower_kev=lower,
        upper_kev=upper,
    )


def _segment(
    j1: NDArray[np.float64],
    j2: NDArray[np.float64],
    e1: NDArray[np.float64],
    e2: NDArray[np.float64],
    a: NDArray[np.float64],
    b: NDArray[np.float64],
) -> NDArray[np.float64]:
    with np.errstate(all="ignore"):
        gamma = np.log(j2 / j1) / np.log(e2 / e1)
        exponent = gamma + 1.0
        power = j1 * e1 / exponent * ((b / e1) ** exponent - (a / e1) ** exponent)
        power = np.where(np.isclose(exponent, 0.0), j1 * e1 * np.log(b / a), power)
        linear = j1 * (b - a) + (j2 - j1) / (e2 - e1) * ((b - e1) ** 2 - (a - e1) ** 2) / 2.0
    both_positive = (j1 > 0.0) & (j2 > 0.0)
    value = np.where(both_positive, power, linear)
    value = np.where(np.isnan(j1) | np.isnan(j2), np.nan, value)
    return np.asarray(np.where(b > a, value, 0.0), dtype=np.float64)


def _flat(j: NDArray[np.float64], a: NDArray[np.float64], b: NDArray[np.float64]) -> NDArray[np.float64]:
    width = np.clip(b - a, 0.0, None)
    return np.asarray(np.where(width > 0.0, j * width, 0.0), dtype=np.float64)


def integral_flux_pfu(day: SgpsDay, threshold_mev: float) -> NDArray[np.float64]:
    threshold = threshold_mev * 1000.0
    eff = np.sqrt(day.lower_kev * day.upper_kev)
    flux = day.diff_flux
    channels = flux.shape[2]
    per_sensor = _flat(flux[:, :, 0], np.maximum(threshold, day.lower_kev[:, 0]), eff[:, 0])
    for c in range(channels - 1):
        e1, e2 = eff[:, c], eff[:, c + 1]
        per_sensor = per_sensor + _segment(flux[:, :, c], flux[:, :, c + 1], e1, e2, np.maximum(e1, threshold), e2)
    last = channels - 1
    per_sensor = per_sensor + _flat(flux[:, :, last], np.maximum(threshold, eff[:, last]), day.upper_kev[:, last])
    valid = ~np.isnan(per_sensor)
    count = valid.sum(axis=1)
    total = np.where(valid, per_sensor, 0.0).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = total / count
    return np.asarray(np.where(count > 0, mean, np.nan), dtype=np.float64)
