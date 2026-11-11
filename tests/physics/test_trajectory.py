from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import numpy as np

from evarisk.orbit.oem import parse_oem
from evarisk.orbit.trajectory import from_oem, saa_passes
from evarisk.types import Provenance, Window

EPH = parse_oem(Path("tests/fixtures/nasa_oem/2024-05-12_ISS.OEM_J2K_EPH.txt").read_text(encoding="utf-8"))
PROV = Provenance(
    raw_id="fixture",
    source="nasa_oem",
    product="oem",
    published_at=EPH.header.creation_date,
    published_at_kind="exact",
    data_version="oem fixture",
    url="fixture",
)
DAY = Window(start=datetime(2024, 5, 11, 0, 0, tzinfo=UTC), end=datetime(2024, 5, 12, 0, 0, tzinfo=UTC))


def test_geodetic_track_is_physical() -> None:
    tr = from_oem(EPH, PROV, DAY, pad=timedelta(0))
    assert len(tr.times) == 24 * 60 + 1
    # HA 418.9 км в заголовке OEM задана над сферой 6378.137 км; над эллипсоидом WGS84 на широте 51.6° высота
    # больше на 13 км, поэтому верхняя граница 435 км.
    assert 400 < tr.alt_km.min() and tr.alt_km.max() < 435
    assert 51.4 < np.abs(tr.lat_deg).max() < 51.8
    assert -180 < tr.lon_deg.min() and tr.lon_deg.max() <= 180


def test_sunlit_fraction_and_eclipse_length() -> None:
    tr = from_oem(EPH, PROV, DAY, pad=timedelta(0))
    frac = tr.sunlit.mean()
    assert 0.58 <= frac <= 1.0
    longest = 0
    run = 0
    for lit in tr.sunlit:
        run = 0 if lit else run + 1
        longest = max(longest, run)
    assert longest <= 38


def test_magnetic_latitude_reaches_high_values() -> None:
    tr = from_oem(EPH, PROV, DAY, pad=timedelta(0))
    finite = np.isfinite(tr.mlat_deg)
    assert finite.mean() > 0.8
    assert np.nanmax(np.abs(tr.mlat_deg)) >= 60.0
    assert 3.5 <= np.nanmax(tr.l_shell) <= 9.0
    assert tr.provider == "oem" and tr.epoch == EPH.header.creation_date


def test_field_magnitude_has_south_atlantic_minimum() -> None:
    tr = from_oem(EPH, PROV, DAY, pad=timedelta(0))
    assert 15000 < tr.b_total_nt.min() < 26000 and 40000 < tr.b_total_nt.max() < 60000
    i = int(np.argmin(tr.b_total_nt))
    assert -60 < tr.lat_deg[i] < 5 and -90 < tr.lon_deg[i] < 40
    assert tr.in_saa.dtype == bool and len(tr.in_saa) == len(tr.times)
    assert tr.in_saa.any() and tr.in_saa.mean() < 0.25
    assert np.all(tr.lat_deg[tr.in_saa] < 12) and np.all(tr.lat_deg[tr.in_saa] > -55)
    assert np.all(tr.lon_deg[tr.in_saa] > -120) and np.all(tr.lon_deg[tr.in_saa] < 45)
    assert not any("ЮАА" in lim for lim in tr.limitations)


def test_position_vectors_are_on_iss_orbit() -> None:
    tr = from_oem(EPH, PROV, DAY, pad=timedelta(0))
    assert tr.r_km.shape == (len(tr.times), 3)
    norms = np.linalg.norm(tr.r_km, axis=1)
    assert 6700 < norms.min() and norms.max() < 6830


def test_saa_passes_cover_every_flagged_minute_once_per_orbit() -> None:
    tr = from_oem(EPH, PROV, DAY, pad=timedelta(0))
    passes = saa_passes(tr)
    # Контур 24 000 нТл на 400 км тянется от Южной Америки до Африки, и при наклонении 51.6° станция задевает его
    # на большинстве из 15.5 витков в сутки; период 93 мин, проходы не чаще одного за виток.
    assert 8 <= len(passes) <= 16
    minutes = sum(int((p.end - p.start).total_seconds() // 60) for p in passes)
    assert minutes == int(tr.in_saa.sum())
    for a, b in pairwise(passes):
        assert b.start - a.end >= timedelta(minutes=60)
    for p in passes:
        assert p.start < p.min_at < p.end + timedelta(minutes=1) and p.min_b_nt < 24000.0
        assert 15000 < p.min_b_nt
