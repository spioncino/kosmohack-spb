from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from evarisk.parsers.donki import (
    Notification,
    Unparsed,
    cme_arrivals,
    gst_kp_observations,
    parse_notifications,
    sep_crossings,
    sep_ends,
    tolerant,
)

FIX = Path("tests/fixtures/donki/notifications_2024-05-09_2024-05-12.json")
FIX_RETROSPECTIVE = Path("tests/fixtures/donki/notifications_gst_2025-11-06.json")
FIX_HAD = Path("tests/fixtures/donki/notifications_gst_2024-10-08.json")


def test_gst_kp_observations_from_gannon_storm_bodies() -> None:
    obs = gst_kp_observations(parse_notifications(FIX.read_bytes()))
    assert len(obs) == 7
    assert [(o.start, o.kp, o.notification.message_id) for o in obs] == [
        (datetime(2024, 5, 10, 15, tzinfo=UTC), 7.67, "20240510-AL-013"),
        (datetime(2024, 5, 10, 15, tzinfo=UTC), 7.67, "20240510-AL-014"),
        (datetime(2024, 5, 10, 18, tzinfo=UTC), 8.67, "20240510-AL-015"),
        (datetime(2024, 5, 10, 18, tzinfo=UTC), 8.67, "20240510-AL-016"),
        (datetime(2024, 5, 10, 21, tzinfo=UTC), 9.0, "20240511-AL-002"),
        (datetime(2024, 5, 10, 21, tzinfo=UTC), 9.0, "20240511-AL-003"),
        (datetime(2024, 5, 11, 9, tzinfo=UTC), 9.0, "20240511-AL-015"),
    ]
    assert all(o.end - o.start == timedelta(hours=3) for o in obs)
    assert obs[0].notification.issued_at == datetime(2024, 5, 10, 18, 44, tzinfo=UTC)
    assert obs[-1].notification.issued_at == datetime(2024, 5, 11, 15, 14, tzinfo=UTC)
    assert all(o.notification.issued_at >= o.start for o in obs)


def test_gst_retrospective_wording_without_has_is_read() -> None:
    # Итоговое сообщение 20251106-AL-009 пишет «Kp index reached level 6.67» без «has».
    # Период и Kp те же, что у сообщения AL-008.
    obs = gst_kp_observations(parse_notifications(FIX_RETROSPECTIVE.read_bytes()))
    assert [(o.start, o.kp, o.notification.message_id) for o in obs] == [
        (datetime(2025, 11, 5, 21, tzinfo=UTC), 6.0, "20251106-AL-001"),
        (datetime(2025, 11, 5, 21, tzinfo=UTC), 6.0, "20251106-AL-002"),
        (datetime(2025, 11, 6, 3, tzinfo=UTC), 6.67, "20251106-AL-008"),
        (datetime(2025, 11, 6, 3, tzinfo=UTC), 6.67, "20251106-AL-009"),
    ]


def test_gst_wording_had_reached_is_read() -> None:
    # Сообщение 20241008-AL-004 пишет «Kp index had reached level 6.67 (strong)» за период 8 октября 00:00–03:00.
    (obs,) = gst_kp_observations(parse_notifications(FIX_HAD.read_bytes()))
    assert (obs.start, obs.kp, obs.notification.message_id) == (
        datetime(2024, 10, 8, 0, tzinfo=UTC),
        6.67,
        "20241008-AL-004",
    )


def test_tolerant_skips_the_unreadable_message_names_it_and_keeps_the_rest() -> None:
    # Одно сообщение в незнакомой формулировке не лишает расчёт остальных семи наблюдений Kp бури 10–11 мая.
    broken = Notification(
        message_id="20240510-AL-099",
        message_type="GST",
        issued_at=datetime(2024, 5, 10, 18, 44, tzinfo=UTC),
        url="u",
        body="## Summary:\n\nGeomagnetic storm is expected.\n",
    )
    notes = [*parse_notifications(FIX.read_bytes()), broken]
    with pytest.raises(ValueError):
        gst_kp_observations(notes)
    skipped: list[Unparsed] = []
    obs = tolerant(gst_kp_observations, notes, skipped)
    assert len(obs) == 7
    assert [(u.message_id, u.message_type, u.issued_at) for u in skipped] == [
        ("20240510-AL-099", "GST", datetime(2024, 5, 10, 18, 44, tzinfo=UTC))
    ]
    assert "нет строки о Kp" in skipped[0].reason


def test_gst_without_kp_sentence_raises() -> None:
    note = Notification(
        message_id="x",
        message_type="GST",
        issued_at=datetime(2024, 5, 10, 18, 44, tzinfo=UTC),
        url="u",
        body="## Summary:\n\nGeomagnetic storm is expected.\n",
    )
    with pytest.raises(ValueError):
        gst_kp_observations([note])


def test_gst_kp_ignores_other_message_types() -> None:
    notes = [n for n in parse_notifications(FIX.read_bytes()) if n.message_type != "GST"]
    assert notes and gst_kp_observations(notes) == []


def test_parse_gannon_storm_notifications() -> None:
    notes = parse_notifications(FIX.read_bytes())
    assert len(notes) == 49
    assert [n.issued_at for n in notes] == sorted(n.issued_at for n in notes)
    first_gst = next(n for n in notes if n.message_type == "GST")
    assert first_gst.issued_at == datetime(2024, 5, 10, 18, 44, tzinfo=UTC)
    cme = next(n for n in notes if n.message_id == "20240509-AL-010")
    assert cme.message_type == "CME" and cme.issued_at == datetime(2024, 5, 9, 21, 24, tzinfo=UTC)
    assert cme.url == "https://kauai.ccmc.gsfc.nasa.gov/DONKI/view/Alert/30653/1"
    assert cme.body.startswith("## Community Coordinated Modeling Center Database Of Notifications")
    assert {"FLR", "SEP", "CME", "GST", "IPS", "MPC"} <= {n.message_type for n in notes}
    assert all(n.issued_at.tzinfo is not None for n in notes)


def test_empty_list_is_empty() -> None:
    assert parse_notifications(b"[]") == []


def test_non_list_json_raises() -> None:
    with pytest.raises(ValueError):
        parse_notifications(b'{"messageType": "CME"}')


def test_message_without_issue_time_raises() -> None:
    row = b'[{"messageType": "CME", "messageID": "x", "messageURL": "u", "messageBody": "b"}]'
    with pytest.raises(ValueError):
        parse_notifications(row)


def _note(message_type: str, body: str, issued: datetime | None = None, message_id: str = "x") -> Notification:
    return Notification(
        message_id=message_id,
        message_type=message_type,
        issued_at=issued or datetime(2024, 5, 11, 3, 0, tzinfo=UTC),
        url="u",
        body=body,
    )


def test_sep_crossings_from_goes_messages_only() -> None:
    crossings = sep_crossings(parse_notifications(FIX.read_bytes()))
    assert [
        (c.energy_mev, c.threshold_pfu, c.start, c.detector, c.issued_at, c.notification.message_id, c.confirmations)
        for c in crossings
    ] == [
        (
            10.0,
            10.0,
            datetime(2024, 5, 10, 13, 35, tzinfo=UTC),
            "GOES",
            datetime(2024, 5, 10, 13, 46, tzinfo=UTC),
            "20240510-AL-004",
            (datetime(2024, 5, 10, 13, 46, tzinfo=UTC), datetime(2024, 5, 10, 14, 12, tzinfo=UTC)),
        ),
        (
            10.0,
            10.0,
            datetime(2024, 5, 11, 2, 10, tzinfo=UTC),
            "GOES",
            datetime(2024, 5, 11, 2, 46, tzinfo=UTC),
            "20240511-AL-007",
            (datetime(2024, 5, 11, 2, 46, tzinfo=UTC),),
        ),
        (
            100.0,
            1.0,
            datetime(2024, 5, 11, 2, 10, tzinfo=UTC),
            "GOES",
            datetime(2024, 5, 11, 2, 30, tzinfo=UTC),
            "20240511-AL-006",
            (datetime(2024, 5, 11, 2, 30, tzinfo=UTC), datetime(2024, 5, 11, 2, 46, tzinfo=UTC)),
        ),
    ]
    # Сообщения STEREO A и SOHO с дифференциальными порогами 10^(-1) pfu/MeV к Земле не относятся.
    stereo_soho = {"20240509-AL-004", "20240510-AL-008", "20240510-AL-009", "20240511-AL-008", "20240511-AL-013"}
    assert not {c.notification.message_id for c in crossings} & stereo_soho


def test_sep_ends_from_dip_sentence() -> None:
    ends = sep_ends(parse_notifications(FIX.read_bytes()))
    assert [(e.energy_mev, e.threshold_pfu, e.time, e.issued_at, e.notification.message_id) for e in ends] == [
        (
            10.0,
            10.0,
            datetime(2024, 5, 11, 1, 10, tzinfo=UTC),
            datetime(2024, 5, 11, 2, 46, tzinfo=UTC),
            "20240511-AL-007",
        )
    ]


def test_sep_crossing_with_unknown_goes_threshold_raises() -> None:
    body = (
        "Solar energetic particle event detected by GOES.  "
        "The flux of > 50 MeV protons exceeds 1 pfu starting at 2024-05-11T02:10Z."
    )
    with pytest.raises(ValueError):
        sep_crossings([_note("SEP", body)])


def test_sep_crossing_in_pfu_for_non_goes_detector_raises() -> None:
    body = (
        "Solar energetic particle event detected by SOHO.  "
        "The flux of > 10 MeV protons exceeds 10 pfu starting at 2024-05-11T02:10Z."
    )
    with pytest.raises(ValueError):
        sep_crossings([_note("SEP", body)])


def test_sep_crossing_after_issue_raises() -> None:
    body = (
        "Solar energetic particle event detected by GOES.  "
        "The flux of > 10 MeV protons exceeds 10 pfu starting at 2024-05-11T04:10Z."
    )
    with pytest.raises(ValueError):
        sep_crossings([_note("SEP", body)])


def test_sep_messages_without_goes_thresholds_give_nothing() -> None:
    notes = [n for n in parse_notifications(FIX.read_bytes()) if n.message_type != "SEP"]
    assert notes and sep_crossings(notes) == [] and sep_ends(notes) == []


def test_cme_arrivals_near_earth_with_kp_ranges() -> None:
    arrivals = cme_arrivals(parse_notifications(FIX.read_bytes()))
    assert [
        (a.notification.message_id, a.issued_at, a.arrival, a.uncertainty_hours, a.kp_min, a.kp_max) for a in arrivals
    ] == [
        (
            "20240509-AL-005",
            datetime(2024, 5, 9, 16, 39, tzinfo=UTC),
            datetime(2024, 5, 11, 6, 42, tzinfo=UTC),
            7.0,
            5.0,
            7.0,
        ),
        (
            "20240509-AL-006",
            datetime(2024, 5, 9, 17, 4, tzinfo=UTC),
            datetime(2024, 5, 11, 5, 2, tzinfo=UTC),
            7.0,
            6.0,
            8.0,
        ),
        (
            "20240509-AL-009",
            datetime(2024, 5, 9, 20, 3, tzinfo=UTC),
            datetime(2024, 5, 10, 23, 46, tzinfo=UTC),
            7.0,
            6.0,
            8.0,
        ),
        (
            "20240509-AL-010",
            datetime(2024, 5, 9, 21, 24, tzinfo=UTC),
            datetime(2024, 5, 10, 13, 3, tzinfo=UTC),
            7.0,
            8.0,
            9.0,
        ),
        (
            "20240509-AL-011",
            datetime(2024, 5, 9, 23, 35, tzinfo=UTC),
            datetime(2024, 5, 11, 7, 55, tzinfo=UTC),
            7.0,
            5.0,
            7.0,
        ),
        (
            "20240510-AL-003",
            datetime(2024, 5, 10, 13, 38, tzinfo=UTC),
            datetime(2024, 5, 12, 14, 0, tzinfo=UTC),
            7.0,
            5.0,
            7.0,
        ),
        (
            "20240511-AL-012",
            datetime(2024, 5, 11, 14, 23, tzinfo=UTC),
            datetime(2024, 5, 13, 4, 59, tzinfo=UTC),
            7.0,
            5.0,
            7.0,
        ),
    ]


def test_cme_arrival_without_kp_range_keeps_none() -> None:
    body = (
        "Simulations indicate that the flank of the CME will reach NASA missions near Earth at about "
        "2026-09-17T09:30Z (plus minus 7 hours)."
    )
    (arrival,) = cme_arrivals([_note("CME", body, issued=datetime(2026, 9, 14, 16, 2, tzinfo=UTC))])
    assert arrival.arrival == datetime(2026, 9, 17, 9, 30, tzinfo=UTC) and arrival.uncertainty_hours == 7.0
    assert arrival.kp_min is None and arrival.kp_max is None


def test_cme_kp_range_phrased_with_of_is_read() -> None:
    body = (
        "The combined leading edge or flank of the CMEs may reach NASA missions near Earth at about "
        "2024-03-24T17:55Z (plus minus 7 hours), with the roughly estimated expected range of the maximum Kp index "
        "of 6-8 (moderate to severe)."
    )
    (arrival,) = cme_arrivals([_note("CME", body, issued=datetime(2024, 3, 23, 19, 34, tzinfo=UTC))])
    assert (arrival.kp_min, arrival.kp_max) == (6.0, 8.0)


def test_cme_message_with_two_earth_arrivals_raises() -> None:
    sentence = (
        "Simulations indicate that the CME will reach NASA missions near Earth at about {t} (plus minus 7 hours)."
    )
    body = sentence.format(t="2024-05-10T13:03Z") + " " + sentence.format(t="2024-05-10T15:03Z")
    with pytest.raises(ValueError):
        cme_arrivals([_note("CME", body)])


def test_cme_reversed_kp_range_raises() -> None:
    body = (
        "Simulations indicate that the CME will reach NASA missions near Earth at about 2024-05-10T13:03Z "
        "(plus minus 7 hours). The roughly estimated expected range of the maximum Kp index is 9-8 (severe)."
    )
    with pytest.raises(ValueError):
        cme_arrivals([_note("CME", body)])
