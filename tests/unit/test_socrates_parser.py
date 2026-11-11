from datetime import UTC, datetime
from pathlib import Path

import pytest

from evarisk.parsers.socrates import data_as_of, parse_socrates

HTML = Path("tests/fixtures/socrates/table_iss_2026-09-17.html").read_text(encoding="utf-8", errors="replace")


def test_data_as_of() -> None:
    assert data_as_of(HTML) == datetime(2026, 9, 17, 22, 14, 23, tzinfo=UTC)


def test_report_and_first_conjunction() -> None:
    rep = parse_socrates(HTML)
    assert rep.data_as_of == datetime(2026, 9, 17, 22, 14, 23, tzinfo=UTC)
    assert rep.interval_start == datetime(2026, 9, 17, 22, 0, tzinfo=UTC)
    assert rep.interval_stop == datetime(2026, 9, 24, 22, 0, tzinfo=UTC)
    assert rep.threshold_km == 5.0 and len(rep.conjunctions) == 3
    c = rep.conjunctions[0]
    assert (c.primary_catnr, c.secondary_catnr, c.secondary_name) == (25544, 39469, "SMDC ONE 2.4")
    assert c.tca == datetime(2026, 9, 21, 21, 20, 21, 992000, tzinfo=UTC)
    assert (c.min_range_km, c.rel_speed_kms, c.max_probability, c.dilution_km) == (1.732, 15.319, 3.045e-05, 1.197)


def test_all_conjunctions_are_with_iss_and_within_interval() -> None:
    rep = parse_socrates(HTML)
    assert [c.secondary_catnr for c in rep.conjunctions] == [39469, 60488, 64888]
    assert all(c.primary_catnr == 25544 for c in rep.conjunctions)
    assert all(rep.interval_start <= c.tca <= rep.interval_stop for c in rep.conjunctions)
    assert all(c.min_range_km <= rep.threshold_km for c in rep.conjunctions)


def test_missing_markers_raise() -> None:
    with pytest.raises(ValueError):
        data_as_of("<html></html>")
    with pytest.raises(ValueError):
        parse_socrates("<html>Data current as of 2026 Sep 17 22:14:23 UTC</html>")
