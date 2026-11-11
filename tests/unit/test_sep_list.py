from datetime import UTC, datetime
from pathlib import Path

import pytest

from evarisk.parsers.sep_list import parse_sep_page

HTML = Path("tests/fixtures/swpc_spe/sep_page_code.html").read_text(encoding="utf-8", errors="replace")


def test_2024_events_from_official_list() -> None:
    events = {e.begin: e for e in parse_sep_page(HTML)}
    may10 = events[datetime(2024, 5, 10, 13, 35, tzinfo=UTC)]
    assert may10.maximum == datetime(2024, 5, 10, 17, 45, tzinfo=UTC) and may10.peak_pfu == 208
    mar23 = events[datetime(2024, 3, 23, 8, 15, tzinfo=UTC)]
    assert mar23.maximum == datetime(2024, 3, 23, 18, 20, tzinfo=UTC) and mar23.peak_pfu == 956
    jun8 = events[datetime(2024, 6, 8, 2, 55, tzinfo=UTC)]
    assert jun8.maximum == datetime(2024, 6, 8, 8, 0, tzinfo=UTC) and jun8.peak_pfu == 1030
    may11 = events[datetime(2024, 5, 11, 2, 10, tzinfo=UTC)]
    assert may11.maximum == datetime(2024, 5, 11, 9, 10, tzinfo=UTC) and may11.peak_pfu == 116


def test_all_fourteen_events_of_2024_are_present() -> None:
    begins = sorted(e.begin for e in parse_sep_page(HTML) if e.begin.year == 2024)
    assert len(begins) == 14
    assert begins[0] == datetime(2024, 1, 3, 20, 5, tzinfo=UTC)
    assert begins[-1] == datetime(2024, 11, 21, 19, 25, tzinfo=UTC)


def test_row_without_closing_tag_still_yields_both_events() -> None:
    events = {e.begin: e for e in parse_sep_page(HTML)}
    jan29 = events[datetime(2024, 1, 29, 6, 15, tzinfo=UTC)]
    feb9 = events[datetime(2024, 2, 9, 15, 30, tzinfo=UTC)]
    assert jan29.maximum == datetime(2024, 1, 29, 18, 5, tzinfo=UTC) and jan29.peak_pfu == 137
    assert feb9.maximum == datetime(2024, 2, 9, 23, 55, tzinfo=UTC) and feb9.peak_pfu == 187


def test_maximum_may_fall_on_a_later_day() -> None:
    events = {e.begin: e for e in parse_sep_page(HTML)}
    oct9 = events[datetime(2024, 10, 9, 5, 5, tzinfo=UTC)]
    assert oct9.maximum == datetime(2024, 10, 10, 15, 15, tzinfo=UTC)


def test_list_starts_in_1976_and_is_sorted() -> None:
    events = parse_sep_page(HTML)
    assert events[0].begin == datetime(1976, 4, 30, 21, 20, tzinfo=UTC)
    assert events[0].maximum == datetime(1976, 5, 1, 17, 0, tzinfo=UTC) and events[0].peak_pfu == 12
    assert [e.begin for e in events] == sorted(e.begin for e in events)
    assert all(e.begin.tzinfo is not None and e.maximum.tzinfo is not None for e in events)


def test_page_is_reproduced_as_listed_including_its_one_inverted_event() -> None:
    inverted = [e for e in parse_sep_page(HTML) if e.maximum < e.begin]
    assert [(e.begin, e.maximum, e.peak_pfu) for e in inverted] == [
        (datetime(2014, 2, 25, 13, 55, tzinfo=UTC), datetime(2014, 2, 25, 8, 45, tzinfo=UTC), 103.0)
    ]


def test_page_without_event_table_raises() -> None:
    with pytest.raises(ValueError):
        parse_sep_page("<html><body><table><tr><td>N/A</td><td>N/A</td><td>N/A</td></tr></table></body></html>")
