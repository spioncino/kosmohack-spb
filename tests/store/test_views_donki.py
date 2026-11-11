import json
from datetime import UTC, datetime
from pathlib import Path

from evarisk.parsers.donki import parse_notifications
from evarisk.store.asof import AsOf
from evarisk.store.raw import RawStore
from evarisk.store.views import unreadable_records
from evarisk.store.views_donki import DONKI_VERSION, cme_kp_forecast, sep_state_as_known, skipped_messages
from evarisk.store.views_kp import kp_as_known

F = datetime(2026, 9, 18, tzinfo=UTC)
DONKI = Path("tests/fixtures/donki/notifications_2024-05-09_2024-05-12.json")


def seed(tmp_path: Path) -> RawStore:
    store = RawStore(tmp_path)
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


def t(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2024, 5, day, hour, minute, tzinfo=UTC)


def test_state_at_16_00_knows_only_the_10_mev_event_since_13_35(tmp_path: Path) -> None:
    (state,) = sep_state_as_known(AsOf(seed(tmp_path), t(10, 16)), t(10, 0), t(12, 0))
    assert (state.start, state.end) == (t(10, 13, 35), t(12, 0))
    assert state.p10_event_since == t(10, 13, 35) and state.p100_event_since is None
    assert state.p10_event is not None
    assert state.p10_event.issued_at == t(10, 13, 46) and state.p10_event.confirmed_at == t(10, 14, 12)
    assert state.p10_event.message_id == "20240510-AL-004" and state.p10_event.threshold_pfu == 10.0
    assert state.data_version == DONKI_VERSION
    # D81: провенанс это сообщения события: о начале (13:46) и последнее подтверждение (14:12).
    assert [(p.data_version, p.published_at) for p in state.provenance] == [
        ("сообщение 20240510-AL-004", t(10, 13, 46)),
        ("сообщение 20240510-AL-005", t(10, 14, 12)),
    ]


def test_state_at_03_00_knows_both_events_and_the_dip_between_them(tmp_path: Path) -> None:
    states = sep_state_as_known(AsOf(seed(tmp_path), t(11, 3)), t(10, 0), t(12, 0))
    assert [(s.start, s.end, s.p10_event_since, s.p100_event_since) for s in states] == [
        (t(10, 13, 35), t(11, 1, 10), t(10, 13, 35), None),
        (t(11, 2, 10), t(12, 0), t(11, 2, 10), t(11, 2, 10)),
    ]
    both = states[1]
    assert both.p10_event is not None and both.p100_event is not None
    assert both.p10_event.issued_at == t(11, 2, 46) and both.p10_event.confirmed_at == t(11, 2, 46)
    assert both.p10_event.message_id == "20240511-AL-007"
    assert both.p100_event.issued_at == t(11, 2, 30) and both.p100_event.confirmed_at == t(11, 2, 46)
    assert both.p100_event.message_id == "20240511-AL-006" and both.p100_event.threshold_pfu == 1.0


def test_state_provenance_names_only_the_messages_of_its_own_events(tmp_path: Path) -> None:
    # D81: отрезок с 11 мая 02:10 задан сообщениями 02:30 и 02:46 о новых событиях; сообщения 10 мая о событии,
    # окончившемся в 01:10, к нему не относятся, а первое сообщение файла не подставляется в чужой отрезок.
    first, second = sep_state_as_known(AsOf(seed(tmp_path), t(11, 3)), t(10, 0), t(12, 0))
    assert {p.data_version for p in first.provenance} == {"сообщение 20240510-AL-004", "сообщение 20240510-AL-005"}
    assert {p.data_version for p in second.provenance} == {"сообщение 20240511-AL-006", "сообщение 20240511-AL-007"}
    assert second.p100_event is not None and [p.data_version for p in second.p100_event.provenance] == [
        "сообщение 20240511-AL-006",
        "сообщение 20240511-AL-007",
    ]
    assert all(p.published_at is not None and p.published_at <= t(11, 3) for p in second.provenance)


def test_state_before_first_message_is_empty(tmp_path: Path) -> None:
    assert sep_state_as_known(AsOf(seed(tmp_path), t(10, 12)), t(10, 0), t(12, 0)) == []


def test_state_is_clipped_to_the_requested_range(tmp_path: Path) -> None:
    (state,) = sep_state_as_known(AsOf(seed(tmp_path), t(11, 3)), t(11, 4), t(11, 10))
    assert (state.start, state.end) == (t(11, 4), t(11, 10))
    assert state.p10_event_since == t(11, 2, 10) and state.p100_event_since == t(11, 2, 10)


def test_dip_reported_later_is_unknown_at_earlier_cutoff(tmp_path: Path) -> None:
    (state,) = sep_state_as_known(AsOf(seed(tmp_path), t(11, 2)), t(11, 0), t(11, 4))
    assert (state.start, state.end) == (t(11, 0), t(11, 4))
    assert state.p10_event_since == t(10, 13, 35) and state.p100_event_since is None


def test_cme_kp_forecast_takes_the_maximum_over_active_arrivals(tmp_path: Path) -> None:
    s = cme_kp_forecast(AsOf(seed(tmp_path), t(10, 0)), t(10, 0), t(11, 0))
    assert s is not None and s.kind == "external_forecast" and s.data_version == DONKI_VERSION
    assert s.starts == [t(10, 6), t(10, 9), t(10, 12), t(10, 15), t(10, 18), t(10, 21), t(11, 0)]
    assert s.kp.tolist() == [9.0, 9.0, 9.0, 9.0, 9.0, 8.0, 8.0]
    assert s.notes[0] == (
        "прогноз прихода CME, DONKI/WSA-ENLIL, выпуск 2024-05-09 21:24 UTC, приход 2024-05-10 13:03 ± 7 ч, Kp 8–9"
    )
    assert "приход 2024-05-10 23:46 ± 7 ч, Kp 6–8" in s.notes[5]
    assert [p.product for p in s.provenance] == ["notifications"]


def test_cme_kp_forecast_uses_only_messages_issued_by_the_cutoff(tmp_path: Path) -> None:
    store = seed(tmp_path)
    before = cme_kp_forecast(AsOf(store, t(9, 21)), t(10, 0), t(11, 0))
    assert before is not None and t(10, 12) not in before.starts
    assert dict(zip(before.starts, before.kp.tolist(), strict=True))[t(10, 18)] == 8.0
    assert cme_kp_forecast(AsOf(store, t(9, 12)), t(10, 0), t(11, 0)) is None
    assert cme_kp_forecast(AsOf(store, t(10, 0)), t(14, 0), t(15, 0)) is None


def seed_with_unreadable_gst(tmp_path: Path) -> RawStore:
    """Файл бури 10–11 мая с добавленным сообщением GST в незнакомой формулировке от 10 мая 19:00."""
    rows = json.loads(DONKI.read_bytes())
    rows.append(
        {
            "messageID": "20240510-AL-099",
            "messageType": "GST",
            "messageIssueTime": "2024-05-10T19:00Z",
            "messageURL": "https://x.invalid/099",
            "messageBody": "## Summary:\n\nGeomagnetic storm is in progress, details to follow.\n",
        }
    )
    store = RawStore(tmp_path)
    store.put(
        source="donki",
        product="notifications",
        url="https://x.invalid/donki-unreadable.json",
        content=json.dumps(rows).encode(),
        fetched_at=F,
        published_at=t(12, 0),
        published_at_kind="derived",
        content_type="application/json",
        data_version="notifications",
    )
    return store


def test_unreadable_message_is_skipped_and_named_and_the_rest_is_still_read(tmp_path: Path) -> None:
    asof = AsOf(seed_with_unreadable_gst(tmp_path), t(11, 3))
    # Остальные сообщения читаются: Kp 8.67 за 10 мая 18:00–21:00 из GST 20240510-AL-015 и состояние события.
    series = kp_as_known(asof, t(10, 18), t(10, 21))
    assert series is not None and float(series.kp[series.starts.index(t(10, 18))]) == 8.67
    assert len(sep_state_as_known(asof, t(10, 0), t(12, 0))) == 2
    (skipped,) = skipped_messages(asof, t(11, 0), t(12, 0))
    assert (skipped.message_id, skipped.message_type, skipped.issued_at) == ("20240510-AL-099", "GST", t(10, 19))


def test_skipped_message_is_not_known_before_its_issue_and_not_named_for_a_far_period(tmp_path: Path) -> None:
    store = seed_with_unreadable_gst(tmp_path)
    assert skipped_messages(AsOf(store, t(10, 18)), t(11, 0), t(12, 0)) == []
    assert skipped_messages(AsOf(store, None), t(30, 0), t(31, 0)) == []


def test_unreadable_notifications_file_is_skipped_and_named(tmp_path: Path) -> None:
    # Файл уведомлений, который не является JSON (например, HTML-заглушка), не роняет расчёт: он пропускается,
    # а сообщения соседнего настоящего файла читаются.
    store = seed(tmp_path)
    store.put(
        source="donki",
        product="notifications",
        url="https://x.invalid/donki-stub.json",
        content=b"<html><body>Service unavailable</body></html>",
        fetched_at=F,
        published_at=t(11, 1),
        published_at_kind="derived",
        content_type="application/json",
        data_version="notifications",
    )
    asof = AsOf(store, t(11, 3))
    assert len(sep_state_as_known(asof, t(10, 0), t(12, 0))) == 2
    series = kp_as_known(asof, t(10, 18), t(10, 21))
    assert series is not None and float(series.kp[series.starts.index(t(10, 18))]) == 8.67
    unreadable = unreadable_records(asof, t(11, 0), t(12, 0))
    assert [(r.source, r.product) for r in unreadable] == [("donki", "notifications")]
