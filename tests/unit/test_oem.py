from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from evarisk.orbit.oem import creation_date, parse_oem

FIX = Path("tests/fixtures/nasa_oem/2024-05-12_ISS.OEM_J2K_EPH.txt")


def test_header_fields() -> None:
    eph = parse_oem(FIX.read_text(encoding="utf-8"))
    h = eph.header
    assert h.creation_date == datetime(2024, 5, 10, 18, 51, 53, 202000, tzinfo=UTC)
    assert h.originator == "NASA/JSC/FOD/TOPO"
    assert h.object_name == "ISS"
    assert h.ref_frame == "EME2000" and h.time_system == "UTC"
    assert h.start_time == datetime(2024, 5, 10, 12, 0, tzinfo=UTC)
    assert h.stop_time == datetime(2024, 5, 25, 12, 0, tzinfo=UTC)


def test_state_vectors_are_physical() -> None:
    eph = parse_oem(FIX.read_text(encoding="utf-8"))
    assert eph.t_s.shape[0] >= 5000 and eph.r_km.shape == (eph.t_s.shape[0], 3)
    assert eph.v_kms.shape == eph.r_km.shape
    radius = np.linalg.norm(eph.r_km, axis=1)
    assert 6700 < radius.min() and radius.max() < 6830
    speed = np.linalg.norm(eph.v_kms, axis=1)
    assert 7.6 < speed.min() and speed.max() < 7.75
    assert eph.epoch0 == eph.header.start_time and eph.t_s[0] == 0.0
    assert eph.t_s[-1] == (eph.header.stop_time - eph.header.start_time).total_seconds()
    assert np.all(np.diff(eph.t_s) > 0)


def test_last_vector_matches_file_tail() -> None:
    eph = parse_oem(FIX.read_text(encoding="utf-8"))
    assert eph.r_km[-1].tolist() == [4438.834135936870, 110.711059770863, -5147.464862329500]
    assert eph.v_kms[-1].tolist() == [1.66542305167032, 7.29789773173528, 1.59073832900598]


def test_creation_date_from_bytes() -> None:
    assert creation_date(FIX.read_bytes()) == datetime(2024, 5, 10, 18, 51, 53, 202000, tzinfo=UTC)


def test_creation_date_missing_raises() -> None:
    with pytest.raises(ValueError):
        creation_date(b"CCSDS_OEM_VERS = 2.0\nORIGINATOR = X\n")


def test_non_utc_time_system_rejected() -> None:
    text = FIX.read_text(encoding="utf-8").replace("TIME_SYSTEM          = UTC", "TIME_SYSTEM          = TAI")
    with pytest.raises(ValueError):
        parse_oem(text)


def test_header_without_vectors_rejected() -> None:
    text = FIX.read_text(encoding="utf-8")
    head_only = text.split("META_STOP", 1)[0] + "META_STOP\n"
    with pytest.raises(ValueError):
        parse_oem(head_only)


def test_malformed_vector_line_rejected() -> None:
    text = FIX.read_text(encoding="utf-8") + "\n2024-05-25T12:04:00.000 1.0 2.0\n"
    with pytest.raises(ValueError):
        parse_oem(text)
