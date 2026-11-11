from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from evarisk.store.asof import AsOf
from evarisk.store.raw import RawStore
from evarisk.store.views import kp_series, oem_for, proton_series, provenance_of
from evarisk.types import Window

F = datetime(2026, 9, 18, tzinfo=UTC)
GOES = "tests/fixtures/goes/sci_sgps-l2-avg5m_g18_d{day}_v3-0-2.nc"


def put_goes(store: RawStore, day: str, url: str, published_at: datetime) -> None:
    store.put(
        source="goes_sgps",
        product="sgps_l2_avg5m",
        url=url,
        content=Path(GOES.format(day=day)).read_bytes(),
        fetched_at=F,
        published_at=published_at,
        published_at_kind="derived",
        content_type="application/x-netcdf",
        data_version="reprocessed v3-0-2",
    )


def seed(tmp_path: Path) -> RawStore:
    store = RawStore(tmp_path)
    for day in ("20240510", "20240511"):
        put_goes(store, day, f"https://x.invalid/{day}.nc", datetime(2024, 5, int(day[6:]) + 1, 0, 5, tzinfo=UTC))
    store.put(
        source="gfz_kp",
        product="kp",
        url="https://x.invalid/kp.json",
        content=Path("tests/fixtures/gfz/kp_2024-05-10_11.json").read_bytes(),
        fetched_at=F,
        published_at=datetime(2024, 7, 15, tzinfo=UTC),
        published_at_kind="derived",
        content_type="application/json",
        data_version="definitive",
    )
    store.put(
        source="nasa_oem",
        product="oem",
        url="https://x.invalid/oem.txt",
        content=Path("tests/fixtures/nasa_oem/2024-05-12_ISS.OEM_J2K_EPH.txt").read_bytes(),
        fetched_at=F,
        published_at=datetime(2024, 5, 10, 18, 51, 53, tzinfo=UTC),
        published_at_kind="exact",
        content_type="text/plain",
        data_version="oem 2024-05-10",
    )
    return store


def test_proton_series_in_review_mode(tmp_path: Path) -> None:
    s = proton_series(AsOf(seed(tmp_path), None), datetime(2024, 5, 10, tzinfo=UTC), datetime(2024, 5, 11, tzinfo=UTC))
    assert s is not None and len(s.times) == 288 and s.kind == "computed"
    assert np.nanmax(s.p10) > 100 and s.provenance[0].data_version == "reprocessed v3-0-2"
    assert s.diff_flux.shape == (288, 13) and s.energies_mev.shape == (13,)
    assert np.all(np.diff(s.energies_mev) > 0) and 1.0 < s.energies_mev[0] < 3.0 and 300 < s.energies_mev[-1] < 600
    assert s.p10.shape == s.p50.shape == s.p100.shape == (288,)
    assert s.times[0] == datetime(2024, 5, 10, tzinfo=UTC) and s.times[-1] == datetime(2024, 5, 10, 23, 55, tzinfo=UTC)
    assert s.data_version == "reprocessed v3-0-2" and s.note.endswith("(D19)")


def test_proton_series_has_p30_between_p10_and_p50(tmp_path: Path) -> None:
    s = proton_series(AsOf(seed(tmp_path), None), datetime(2024, 5, 10, tzinfo=UTC), datetime(2024, 5, 11, tzinfo=UTC))
    assert s is not None and s.p30.shape == s.p10.shape
    assert np.nanmax(s.p30) < np.nanmax(s.p10)
    assert np.all(np.nan_to_num(s.p50, nan=0.0) <= np.nan_to_num(s.p30, nan=np.inf))
    assert np.all(np.nan_to_num(s.p30, nan=0.0) <= np.nan_to_num(s.p10, nan=np.inf))


def test_proton_series_peak_matches_official_event(tmp_path: Path) -> None:
    s = proton_series(AsOf(seed(tmp_path), None), datetime(2024, 5, 10, tzinfo=UTC), datetime(2024, 5, 11, tzinfo=UTC))
    assert s is not None
    peak = int(np.nanargmax(s.p10))
    assert s.times[peak] == datetime(2024, 5, 10, 17, 45, tzinfo=UTC)
    assert 208 * 0.8 <= float(s.p10[peak]) <= 208 * 1.5
    assert np.all(np.nan_to_num(s.p100, nan=0.0) <= np.nan_to_num(s.p50, nan=np.inf))
    assert np.all(np.nan_to_num(s.p50, nan=0.0) <= np.nan_to_num(s.p10, nan=np.inf))


def test_proton_series_spans_two_days(tmp_path: Path) -> None:
    start, end = datetime(2024, 5, 10, 18, tzinfo=UTC), datetime(2024, 5, 11, 6, tzinfo=UTC)
    s = proton_series(AsOf(seed(tmp_path), None), start, end)
    assert s is not None and len(s.times) == 144 and s.diff_flux.shape == (144, 13)
    assert s.times[0] == start and s.times[-1] == datetime(2024, 5, 11, 5, 55, tzinfo=UTC)
    assert all(a < b for a, b in zip(s.times, s.times[1:], strict=False))
    assert [p.url for p in s.provenance] == ["https://x.invalid/20240510.nc", "https://x.invalid/20240511.nc"]


def test_proton_series_covers_only_available_days(tmp_path: Path) -> None:
    asof = AsOf(seed(tmp_path), datetime(2024, 5, 11, 12, 0, tzinfo=UTC))
    s = proton_series(asof, datetime(2024, 5, 10, 18, tzinfo=UTC), datetime(2024, 5, 11, 6, tzinfo=UTC))
    assert s is not None and len(s.times) == 72 and s.times[-1] == datetime(2024, 5, 10, 23, 55, tzinfo=UTC)
    assert proton_series(asof, datetime(2024, 5, 12, tzinfo=UTC), datetime(2024, 5, 13, tzinfo=UTC)) is None


def test_proton_series_respects_cutoff(tmp_path: Path) -> None:
    asof = AsOf(seed(tmp_path), datetime(2024, 5, 10, 12, 0, tzinfo=UTC))
    assert proton_series(asof, datetime(2024, 5, 10, tzinfo=UTC), datetime(2024, 5, 11, tzinfo=UTC)) is None


def test_proton_series_uses_latest_record_per_day(tmp_path: Path) -> None:
    store = seed(tmp_path)
    put_goes(store, "20240510", "https://x.invalid/again/20240510.nc", datetime(2024, 5, 13, 0, 5, tzinfo=UTC))
    s = proton_series(AsOf(store, None), datetime(2024, 5, 10, tzinfo=UTC), datetime(2024, 5, 11, tzinfo=UTC))
    assert s is not None and len(s.times) == 288
    assert [p.url for p in s.provenance] == ["https://x.invalid/again/20240510.nc"]


def test_kp_series_excluded_in_replay_included_in_review(tmp_path: Path) -> None:
    store = seed(tmp_path)
    replay = AsOf(store, datetime(2024, 5, 10, 12, 0, tzinfo=UTC))
    assert kp_series(replay, datetime(2024, 5, 10, tzinfo=UTC), datetime(2024, 5, 12, tzinfo=UTC)) is None
    s = kp_series(AsOf(store, None), datetime(2024, 5, 10, tzinfo=UTC), datetime(2024, 5, 12, tzinfo=UTC))
    assert s is not None and float(np.max(s.kp)) == 9.0 and s.data_version == "definitive"
    assert s.kind == "observation" and len(s.starts) == 16 and s.kp.shape == (16,)
    assert s.starts[0] == datetime(2024, 5, 10, tzinfo=UTC) and s.provenance[0].source == "gfz_kp"


def test_kp_series_keeps_interval_covering_start(tmp_path: Path) -> None:
    asof = AsOf(seed(tmp_path), None)
    s = kp_series(asof, datetime(2024, 5, 10, 4, tzinfo=UTC), datetime(2024, 5, 10, 8, tzinfo=UTC))
    assert s is not None and s.starts == [datetime(2024, 5, 10, 3, tzinfo=UTC), datetime(2024, 5, 10, 6, tzinfo=UTC)]
    assert kp_series(asof, datetime(2024, 6, 1, tzinfo=UTC), datetime(2024, 6, 2, tzinfo=UTC)) is None


def test_kp_series_takes_latest_record_that_covers_the_range(tmp_path: Path) -> None:
    store = seed(tmp_path)
    # Более поздний по доступности снимок nowcast без интервалов 2024 года не должен вытеснять definitive.
    store.put(
        source="gfz_kp",
        product="kp",
        url="https://x.invalid/kp_nowcast.json",
        content=b'{"Kp":[1.0],"datetime":["2026-09-18T18:00:00Z"],"status":["nowcast"]}',
        fetched_at=datetime(2026, 9, 18, 22, tzinfo=UTC),
        published_at=datetime(2026, 9, 18, 22, tzinfo=UTC),
        published_at_kind="derived",
        content_type="application/json",
        data_version="nowcast",
    )
    asof = AsOf(store, None)
    s = kp_series(asof, datetime(2024, 5, 10, tzinfo=UTC), datetime(2024, 5, 12, tzinfo=UTC))
    assert s is not None and s.data_version == "definitive" and len(s.starts) == 16
    later = kp_series(asof, datetime(2026, 9, 18, 18, tzinfo=UTC), datetime(2026, 9, 18, 21, tzinfo=UTC))
    assert later is not None and later.data_version == "nowcast"


def test_oem_selection_by_availability(tmp_path: Path) -> None:
    store = seed(tmp_path)
    w = Window(start=datetime(2024, 5, 11, 6, 0, tzinfo=UTC), end=datetime(2024, 5, 11, 12, 0, tzinfo=UTC))
    assert oem_for(AsOf(store, datetime(2024, 5, 10, 12, 0, tzinfo=UTC)), w) is None
    found = oem_for(AsOf(store, datetime(2024, 5, 11, 0, 0, tzinfo=UTC)), w)
    assert found is not None and found[1].data_version == "oem 2024-05-10"
    assert found[0].header.start_time == datetime(2024, 5, 10, 12, tzinfo=UTC)


def test_oem_requires_coverage_with_margin(tmp_path: Path) -> None:
    asof = AsOf(seed(tmp_path), None)
    inside = Window(start=datetime(2024, 5, 10, 13, 0, tzinfo=UTC), end=datetime(2024, 5, 10, 15, 0, tzinfo=UTC))
    assert oem_for(asof, inside) is not None
    too_early = Window(start=datetime(2024, 5, 10, 12, 30, tzinfo=UTC), end=datetime(2024, 5, 10, 15, 0, tzinfo=UTC))
    assert oem_for(asof, too_early) is None
    too_late = Window(start=datetime(2024, 5, 25, 9, 0, tzinfo=UTC), end=datetime(2024, 5, 25, 11, 30, tzinfo=UTC))
    assert oem_for(asof, too_late) is None


def test_provenance_of_copies_record_fields(tmp_path: Path) -> None:
    rec = AsOf(seed(tmp_path), None).latest("nasa_oem", "oem")
    assert rec is not None
    p = provenance_of(rec)
    assert (p.raw_id, p.source, p.product, p.url) == (rec.id, "nasa_oem", "oem", "https://x.invalid/oem.txt")
    assert p.published_at == rec.published_at and p.published_at_kind == "exact" and p.data_version == "oem 2024-05-10"
