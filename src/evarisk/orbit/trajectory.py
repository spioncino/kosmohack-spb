import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict

from evarisk.orbit.frames import geodetic_from_gcrs
from evarisk.orbit.interp import hermite_sample
from evarisk.orbit.magnetic import aacgm, b_total_nt, in_saa, l_shell
from evarisk.orbit.oem import OemEphemeris
from evarisk.orbit.sun import is_sunlit, sun_direction_gcrs
from evarisk.types import Provenance, Window

SAA_NOT_MARKED = "ЮАА не размечена: порог поля не сверен (D34)"


@dataclass(frozen=True)
class Trajectory:
    provider: str
    provenance: Provenance
    epoch: datetime
    is_reconstruction: bool
    times: list[datetime]
    r_km: NDArray[np.float64]
    lat_deg: NDArray[np.float64]
    lon_deg: NDArray[np.float64]
    alt_km: NDArray[np.float64]
    sunlit: NDArray[np.bool_]
    mlat_deg: NDArray[np.float64]
    l_shell: NDArray[np.float64]
    b_total_nt: NDArray[np.float64]
    in_saa: NDArray[np.bool_]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class SaaPass:
    """Непрерывный отрезок точек сетки внутри ЮАА: границы, минимум модуля поля и момент минимума."""

    start: datetime
    end: datetime
    min_b_nt: float
    min_at: datetime


def saa_passes(trajectory: Trajectory) -> list[SaaPass]:
    """Проходы через ЮАА по флагу `in_saa`; конец отрезка это время последней точки плюс шаг сетки."""
    times = trajectory.times
    if len(times) < 2:
        return []
    step = times[1] - times[0]
    passes: list[SaaPass] = []
    start: int | None = None
    for i, inside in enumerate([*trajectory.in_saa.tolist(), False]):
        if inside and start is None:
            start = i
        elif not inside and start is not None:
            segment = trajectory.b_total_nt[start:i]
            k = start + int(np.argmin(segment))
            passes.append(
                SaaPass(start=times[start], end=times[i - 1] + step, min_b_nt=float(segment.min()), min_at=times[k])
            )
            start = None
    return passes


class TrajectoryConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    saa_b_threshold_nt: float | None = None
    sources: dict[str, str]


def load_trajectory_config(path: Path = Path("config/trajectory.toml")) -> TrajectoryConfig:
    with path.open("rb") as fh:
        return TrajectoryConfig(**tomllib.load(fh))


def time_grid(window: Window, step: timedelta, pad: timedelta) -> list[datetime]:
    start, end = window.start - pad, window.end + pad
    n = int((end - start) / step)
    return [start + i * step for i in range(n + 1)]


def from_oem(
    eph: OemEphemeris,
    provenance: Provenance,
    window: Window,
    step: timedelta = timedelta(minutes=1),
    pad: timedelta = timedelta(hours=1),
    is_reconstruction: bool = False,
) -> Trajectory:
    """Траектория по OEM на сетке от window.start - pad до window.end + pad с шагом step.

    Эпоха AACGM и IGRF одна на весь интервал: коэффициенты меняются по годам, интервал не длиннее суток.
    """
    times = time_grid(window, step, pad)
    r, _ = hermite_sample(eph, times)
    lat, lon, alt = geodetic_from_gcrs(times, r)
    sunlit = np.array([is_sunlit(r[i], sun_direction_gcrs(t)) for i, t in enumerate(times)], dtype=bool)
    mid = times[len(times) // 2]
    mlat, _, limitations = aacgm(lat, lon, alt, mid)
    b_total = b_total_nt(lat, lon, alt, mid)
    threshold = load_trajectory_config().saa_b_threshold_nt
    if threshold is None:
        limitations.append(SAA_NOT_MARKED)
    return Trajectory(
        provider="oem",
        provenance=provenance,
        epoch=eph.header.creation_date,
        is_reconstruction=is_reconstruction,
        times=times,
        r_km=r,
        lat_deg=lat,
        lon_deg=lon,
        alt_km=alt,
        sunlit=sunlit,
        mlat_deg=mlat,
        l_shell=l_shell(mlat),
        b_total_nt=b_total,
        in_saa=in_saa(b_total, threshold),
        limitations=tuple(limitations),
    )
