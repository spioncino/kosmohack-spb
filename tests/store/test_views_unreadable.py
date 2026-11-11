from datetime import UTC, datetime
from pathlib import Path

from evarisk.parsers.donki import parse_notifications
from evarisk.parsers.swpc_text import issued_at
from evarisk.store.asof import AsOf
from evarisk.store.raw import RawStore
from evarisk.store.views import proton_series, unreadable_records
from evarisk.store.views_kp import NOTE_DONKI_GST, NOTE_ELAPSED_CELL, NOTE_MAX_24H, kp_as_known, kp_forecast

F = datetime(2026, 9, 18, tzinfo=UTC)
FIX = Path("tests/fixtures/swpc_archive")
DONKI = Path("tests/fixtures/donki/notifications_2024-05-09_2024-05-12.json")
GOES = "tests/fixtures/goes/sci_sgps-l2-avg5m_g18_d{day}_v3-0-2.nc"
STUB = b"<html><head><title>503 Service Unavailable</title></head><body>no data</body></html>"


def t(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2024, 5, day, hour, minute, tzinfo=UTC)


def put_swpc(store: RawStore, name: str, product: str, version: str) -> None:
    text = (FIX / name).read_text(encoding="utf-8")
    store.put(
        source="swpc_archive",
        product=product,
        url=f"https://x.invalid/{name}",
        content=text.encode("utf-8"),
        fetched_at=F,
        published_at=issued_at(text),
        published_at_kind="exact",
        content_type="text/plain",
        data_version=version,
    )


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


def put_broken(store: RawStore, *, source: str, product: str, url: str, published_at: datetime, version: str) -> str:
    rec, _ = store.put(
        source=source,
        product=product,
        url=url,
        content=STUB,
        fetched_at=F,
        published_at=published_at,
        published_at_kind="exact",
        content_type="text/plain",
        data_version=version,
    )
    return rec.id


def seed_swpc(tmp_path: Path) -> RawStore:
    store = RawStore(tmp_path)
    put_swpc(store, "202405100030three_day_forecast.txt", "3day_forecast", "issue 0030")
    put_swpc(store, "202405101230three_day_forecast.txt", "3day_forecast", "issue 1230")
    put_swpc(store, "20240510daypre.txt", "daypre", "daily")
    content = DONKI.read_bytes()
    store.put(
        source="donki",
        product="notifications",
        url="https://x.invalid/donki.json",
        content=content,
        fetched_at=F,
        published_at=max(n.issued_at for n in parse_notifications(content)),
        published_at_kind="derived",
        content_type="application/json",
        data_version="notifications",
    )
    return store


def seed_goes(tmp_path: Path) -> RawStore:
    store = RawStore(tmp_path)
    for day in ("20240510", "20240511"):
        put_goes(store, day, f"https://x.invalid/{day}.nc", datetime(2024, 5, int(day[6:]) + 1, 0, 5, tzinfo=UTC))
    return store


def broken_issue(store: RawStore) -> str:
    return put_broken(
        store,
        source="swpc_archive",
        product="3day_forecast",
        url="https://x.invalid/202405101300three_day_forecast.txt",
        published_at=t(10, 13),
        version="issue 1300",
    )


def test_unreadable_three_day_issue_is_skipped_and_kp_as_known_is_unchanged(tmp_path: Path) -> None:
    store = seed_swpc(tmp_path)
    broken = broken_issue(store)
    asof = AsOf(store, t(11, 2))
    s = kp_as_known(asof, t(10, 0), t(11, 0))
    assert s is not None
    got = dict(zip(s.starts, zip(s.kp.tolist(), s.notes, strict=True), strict=True))
    assert got == {
        t(9, 21): (3.0, NOTE_MAX_24H),
        t(10, 0): (3.0, NOTE_ELAPSED_CELL),
        t(10, 3): (3.0, NOTE_ELAPSED_CELL),
        t(10, 6): (2.67, NOTE_ELAPSED_CELL),
        t(10, 9): (2.33, NOTE_ELAPSED_CELL),
        t(10, 15): (7.67, NOTE_DONKI_GST),
        t(10, 18): (8.67, NOTE_DONKI_GST),
        t(10, 21): (9.0, NOTE_DONKI_GST),
    }
    assert broken not in {p.raw_id for p in s.provenance}
    skipped = unreadable_records(asof, t(10, 0), t(11, 0))
    assert [(u.raw_id, u.source, u.product, u.available_at) for u in skipped] == [
        (broken, "swpc_archive", "3day_forecast", t(10, 13))
    ]
    assert "Issued" in skipped[0].reason


def test_unreadable_issue_does_not_stop_kp_forecast(tmp_path: Path) -> None:
    store = seed_swpc(tmp_path)
    broken_issue(store)
    s = kp_forecast(AsOf(store, t(10, 14)), t(11, 4), t(11, 10))
    assert s is not None and s.data_version == "issue 2024-05-10 12:30"
    assert s.starts == [t(11, 3), t(11, 6), t(11, 9)] and list(s.kp) == [7.0, 8.33, 6.67]


def test_unreadable_record_outside_the_period_is_not_named(tmp_path: Path) -> None:
    store = seed_swpc(tmp_path)
    broken_issue(store)
    asof = AsOf(store, t(11, 2))
    kp_as_known(asof, t(10, 0), t(11, 0))
    assert unreadable_records(asof, datetime(2024, 5, 20, tzinfo=UTC), datetime(2024, 5, 21, tzinfo=UTC)) == []
    assert unreadable_records(asof, t(10, 0), t(10, 12)) == []


def test_record_later_than_the_cutoff_is_not_read_and_is_not_named(tmp_path: Path) -> None:
    store = seed_swpc(tmp_path)
    broken_issue(store)
    early = AsOf(store, t(10, 12))
    assert kp_as_known(early, t(10, 0), t(10, 12)) is not None
    assert early.unreadable == {} and unreadable_records(early, t(10, 0), t(10, 12)) == []


def test_unreadable_goes_day_is_skipped_and_the_series_is_still_built(tmp_path: Path) -> None:
    store = seed_goes(tmp_path)
    broken = put_broken(
        store,
        source="goes_sgps",
        product="sgps_l2_avg5m",
        url="https://x.invalid/sci_sgps-l2-avg5m_g18_d20240510_v9-9-9.nc",
        published_at=t(12, 12),
        version="reprocessed v9-9-9",
    )
    asof = AsOf(store, None)
    s = proton_series(asof, t(10, 0), datetime(2024, 5, 13, tzinfo=UTC))
    assert s is not None and len(s.times) == 576
    assert [p.url for p in s.provenance] == ["https://x.invalid/20240510.nc", "https://x.invalid/20240511.nc"]
    skipped = unreadable_records(asof, t(10, 0), datetime(2024, 5, 13, tzinfo=UTC))
    assert [(u.raw_id, u.source, u.product) for u in skipped] == [(broken, "goes_sgps", "sgps_l2_avg5m")]


def test_goes_file_of_another_day_is_not_read(tmp_path: Path) -> None:
    store = seed_goes(tmp_path)
    put_goes(store, "20240511", "https://x.invalid/sci_sgps-l2-avg5m_g18_d20240601_v3-0-2.nc", t(12, 12))
    asof = AsOf(store, None)
    s = proton_series(asof, t(10, 0), t(11, 0))
    assert s is not None and [p.url for p in s.provenance] == ["https://x.invalid/20240510.nc"]
