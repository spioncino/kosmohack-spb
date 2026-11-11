from datetime import datetime

import numpy as np
from numpy.typing import NDArray

from evarisk.orbit.oem import OemEphemeris


def hermite_sample(eph: OemEphemeris, times: list[datetime]) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    t = np.asarray([(x - eph.epoch0).total_seconds() for x in times], dtype=np.float64)
    if t.size and (t.min() < eph.t_s[0] or t.max() > eph.t_s[-1]):
        raise ValueError("запрошенное время вне интервала эфемериды")
    idx = np.clip(np.searchsorted(eph.t_s, t, side="right") - 1, 0, len(eph.t_s) - 2)
    t0, t1 = eph.t_s[idx], eph.t_s[idx + 1]
    h = (t1 - t0)[:, None]
    s = ((t - t0) / (t1 - t0))[:, None]
    p0, p1 = eph.r_km[idx], eph.r_km[idx + 1]
    m0, m1 = eph.v_kms[idx] * h, eph.v_kms[idx + 1] * h
    h00 = 2 * s**3 - 3 * s**2 + 1
    h10 = s**3 - 2 * s**2 + s
    h01 = -2 * s**3 + 3 * s**2
    h11 = s**3 - s**2
    r = h00 * p0 + h10 * m0 + h01 * p1 + h11 * m1
    d00 = 6 * s**2 - 6 * s
    d10 = 3 * s**2 - 4 * s + 1
    d01 = -6 * s**2 + 6 * s
    d11 = 3 * s**2 - 2 * s
    v = (d00 * p0 + d10 * m0 + d01 * p1 + d11 * m1) / h
    return np.asarray(r, dtype=np.float64), np.asarray(v, dtype=np.float64)
