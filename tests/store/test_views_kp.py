from datetime import UTC, date, datetime
from pathlib import Path

from evarisk.parsers.donki import parse_notifications
from evarisk.parsers.swpc_text import issued_at
from evarisk.store.asof import AsOf
from evarisk.store.raw import RawStore
from evarisk.store.views_kp import (
    NOTE_DONKI_GST,
    NOTE_ELAPSED_CELL,
    NOTE_MAX_24H,
    kp_as_known,
    kp_forecast,
    proton_event_probability,
)

F = datetime(2026, 9, 18, tzinfo=UTC)
FIX = Path("tests/fixtures/swpc_archive")
DONKI = Path("tests/fixtures/donki/notifications_2024-05-09_2024-05-12.json")
MAY_11 = date(2024, 5, 11)


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


def put_donki(store: RawStore) -> None:
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


def seed(tmp_path: Path) -> RawStore:
    store = RawStore(tmp_path)
    put_swpc(store, "202405100030three_day_forecast.txt", "3day_forecast", "issue 0030")
    put_swpc(store, "202405101230three_day_forecast.txt", "3day_forecast", "issue 1230")
    put_swpc(store, "20240508geomag_forecast.txt", "geomag_forecast", "daily")
    put_swpc(store, "20240509geomag_forecast.txt", "geomag_forecast", "daily")
    put_swpc(store, "20240510geomag_forecast.txt", "geomag_forecast", "daily")
    put_swpc(store, "20240510daypre.txt", "daypre", "daily")
    put_donki(store)
    return store


def t(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2024, 5, day, hour, minute, tzinfo=UTC)


def test_kp_forecast_takes_latest_issue_available_at_cutoff(tmp_path: Path) -> None:
    store = seed(tmp_path)
    s = kp_forecast(AsOf(store, t(10, 12, 30)), t(11, 4), t(11, 10))
    assert s is not None and s.kind == "external_forecast" and s.data_version == "issue 2024-05-10 12:30"
    assert s.starts == [t(11, 3), t(11, 6), t(11, 9)] and list(s.kp) == [7.0, 8.33, 6.67]
    assert [p.product for p in s.provenance] == ["3day_forecast"] and s.provenance[0].data_version == "issue 1230"
    assert all("выпуск 2024-05-10 12:30" in n for n in s.notes)
    earlier = kp_forecast(AsOf(store, t(10, 0)), t(11, 4), t(11, 10))
    assert earlier is not None and earlier.provenance[0].product == "geomag_forecast"
    assert earlier.data_version == "issue 2024-05-09 22:10" and list(earlier.kp) == [7.0, 8.33, 6.67]
    first = kp_forecast(AsOf(store, t(9, 0)), t(11, 4), t(11, 10))
    assert first is not None and first.data_version == "issue 2024-05-08 22:05"
    assert list(first.kp) == [5.33, 5.33, 5.0]
    assert kp_forecast(AsOf(store, t(8, 12)), t(11, 4), t(11, 10)) is None


def test_kp_forecast_excludes_cells_elapsed_at_issue(tmp_path: Path) -> None:
    s = kp_forecast(AsOf(seed(tmp_path), t(10, 12, 30)), t(10, 0), t(10, 23))
    assert s is not None and s.starts[0] == t(10, 12) and list(s.kp)[:2] == [2.67, 3.67]


def test_kp_as_known_combines_elapsed_cells_donki_and_daily_maximum(tmp_path: Path) -> None:
    s = kp_as_known(AsOf(seed(tmp_path), t(11, 2)), t(10, 0), t(11, 0))
    assert s is not None and s.kind == "observation" and s.data_version == "swpc dated products"
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
    assert t(10, 12) not in got
    assert {p.product for p in s.provenance} == {"3day_forecast", "notifications"}


def test_kp_as_known_filters_donki_messages_by_their_own_issue_time(tmp_path: Path) -> None:
    store = seed(tmp_path)
    at_19 = kp_as_known(AsOf(store, t(10, 19)), t(10, 15), t(10, 23))
    assert at_19 is not None and dict(zip(at_19.starts, at_19.kp.tolist(), strict=True)) == {t(10, 15): 7.67}
    assert at_19.provenance[0].product == "notifications" and at_19.notes == (NOTE_DONKI_GST,)
    at_18 = kp_as_known(AsOf(store, t(10, 18, 30)), t(10, 15), t(10, 23))
    assert at_18 is None
    review = kp_as_known(AsOf(store, None), t(11, 9), t(11, 12))
    assert review is not None and dict(zip(review.starts, review.kp.tolist(), strict=True)) == {t(11, 9): 9.0}


def test_kp_as_known_before_first_issue_is_none(tmp_path: Path) -> None:
    store = seed(tmp_path)
    assert kp_as_known(AsOf(store, t(10, 12)), t(10, 3), t(10, 23)) is None
    only_max = kp_as_known(AsOf(store, t(10, 12)), t(9, 0), t(9, 23))
    assert only_max is not None and set(only_max.kp.tolist()) == {2.0} and len(only_max.starts) == 8
    assert all(n == NOTE_MAX_24H for n in only_max.notes)


def test_proton_event_probability_takes_latest_product_covering_the_day(tmp_path: Path) -> None:
    store = seed(tmp_path)
    at_cutoff = proton_event_probability(AsOf(store, t(10, 12, 30)), MAY_11)
    assert at_cutoff is not None and at_cutoff[0] == 55 and at_cutoff[1].data_version == "issue 1230"
    morning = proton_event_probability(AsOf(store, t(10, 1)), MAY_11)
    assert morning is not None and morning[0] == 50 and morning[1].data_version == "issue 0030"
    evening = proton_event_probability(AsOf(store, t(11, 0)), MAY_11)
    assert evening is not None and evening[0] == 99 and evening[1].product == "daypre"
    assert proton_event_probability(AsOf(store, t(9, 0)), MAY_11) is None
    assert proton_event_probability(AsOf(store, t(11, 0)), date(2024, 5, 14)) is None
    review = proton_event_probability(AsOf(store, None), date(2024, 5, 13))
    assert review is not None and review[0] == 60
