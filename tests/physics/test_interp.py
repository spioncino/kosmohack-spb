from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest

from evarisk.orbit.interp import hermite_sample
from evarisk.orbit.oem import parse_oem

EPH = parse_oem(Path("tests/fixtures/nasa_oem/2024-05-12_ISS.OEM_J2K_EPH.txt").read_text(encoding="utf-8"))


def test_nodes_are_reproduced_exactly() -> None:
    times = [EPH.epoch0 + timedelta(seconds=float(s)) for s in EPH.t_s[:5]]
    r, v = hermite_sample(EPH, times)
    assert np.allclose(r, EPH.r_km[:5], atol=1e-6) and np.allclose(v, EPH.v_kms[:5], atol=1e-9)


def test_midpoints_stay_on_orbit() -> None:
    times = [EPH.epoch0 + timedelta(seconds=float(s) + 120.0) for s in EPH.t_s[:200]]
    r, _ = hermite_sample(EPH, times)
    radius = np.linalg.norm(r, axis=1)
    assert 6700 < radius.min() and radius.max() < 6830
    assert np.all(np.abs(np.diff(radius)) < 5.0)


def test_outside_span_rejected() -> None:
    with pytest.raises(ValueError):
        hermite_sample(EPH, [EPH.header.stop_time + timedelta(minutes=1)])
