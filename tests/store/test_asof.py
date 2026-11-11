from datetime import UTC, datetime
from pathlib import Path

from evarisk.store.asof import AsOf
from evarisk.store.raw import RawStore

F = datetime(2026, 9, 18, tzinfo=UTC)


def seed(tmp_path: Path) -> RawStore:
    store = RawStore(tmp_path)
    store.put(
        source="swpc_archive",
        product="3day_forecast",
        url="u1",
        content=b"a",
        fetched_at=F,
        published_at=datetime(2024, 5, 10, 0, 30, tzinfo=UTC),
        published_at_kind="exact",
        content_type="text/plain",
        data_version="issue 0030",
    )
    store.put(
        source="swpc_archive",
        product="3day_forecast",
        url="u2",
        content=b"b",
        fetched_at=F,
        published_at=datetime(2024, 5, 10, 12, 30, tzinfo=UTC),
        published_at_kind="exact",
        content_type="text/plain",
        data_version="issue 1230",
    )
    store.put(
        source="gfz_kp",
        product="kp",
        url="u3",
        content=b"c",
        fetched_at=F,
        published_at=datetime(2024, 7, 15, tzinfo=UTC),
        published_at_kind="derived",
        content_type="application/json",
        data_version="definitive",
    )
    store.put(
        source="meteor_docs",
        product="imo",
        url="u4",
        content=b"d",
        fetched_at=F,
        published_at=None,
        published_at_kind="unknown",
        content_type="application/pdf",
        data_version="2024",
    )
    return store


def test_cutoff_filters_by_availability(tmp_path: Path) -> None:
    asof = AsOf(seed(tmp_path), datetime(2024, 5, 10, 6, 0, tzinfo=UTC))
    assert [r.data_version for r in asof.records("swpc_archive", "3day_forecast")] == ["issue 0030"]
    assert asof.records("gfz_kp") == []
    assert asof.records("meteor_docs") == []


def test_cutoff_is_inclusive_at_published_at(tmp_path: Path) -> None:
    asof = AsOf(seed(tmp_path), datetime(2024, 5, 10, 12, 30, tzinfo=UTC))
    assert [r.data_version for r in asof.records("swpc_archive", "3day_forecast")] == ["issue 0030", "issue 1230"]


def test_none_means_everything(tmp_path: Path) -> None:
    asof = AsOf(seed(tmp_path), None)
    assert len(asof.records("swpc_archive")) == 2 and len(asof.records("gfz_kp")) == 1
    assert len(asof.records("meteor_docs")) == 1
    latest = asof.latest("swpc_archive", "3day_forecast")
    assert latest is not None and latest.data_version == "issue 1230"


def test_latest_respects_cutoff_and_absence(tmp_path: Path) -> None:
    store = seed(tmp_path)
    latest = AsOf(store, datetime(2024, 5, 10, 6, 0, tzinfo=UTC)).latest("swpc_archive", "3day_forecast")
    assert latest is not None and latest.data_version == "issue 0030"
    assert AsOf(store, datetime(2024, 5, 10, 0, 0, tzinfo=UTC)).latest("swpc_archive", "3day_forecast") is None
    assert AsOf(store, None).latest("nasa_oem", "oem") is None


def test_latest_breaks_ties_by_fetched_at(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    published = datetime(2024, 5, 10, 0, 30, tzinfo=UTC)
    fetches = ((datetime(2026, 9, 18, 1, tzinfo=UTC), "first"), (datetime(2026, 9, 18, 2, tzinfo=UTC), "second"))
    for fetched, version in fetches:
        store.put(
            source="swpc_archive",
            product="3day_forecast",
            url=f"https://x.invalid/{version}",
            content=version.encode(),
            fetched_at=fetched,
            published_at=published,
            published_at_kind="exact",
            content_type="text/plain",
            data_version=version,
        )
    latest = AsOf(store, None).latest("swpc_archive", "3day_forecast")
    assert latest is not None and latest.data_version == "second"


def test_read_returns_stored_bytes(tmp_path: Path) -> None:
    asof = AsOf(seed(tmp_path), None)
    rec = asof.latest("gfz_kp", "kp")
    assert rec is not None and asof.read(rec) == b"c"
