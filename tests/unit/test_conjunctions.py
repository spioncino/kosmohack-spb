from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from evarisk.orbit.trajectory import Trajectory
from evarisk.parsers.socrates import parse_socrates
from evarisk.risk.lines.conjunctions import (
    LIMIT_NO_ARCHIVE,
    LIMIT_OPERATIONAL,
    ConjunctionLine,
    conjunction_level,
    conjunctions_touching,
    is_coorbiting,
    load_conjunction_config,
)
from evarisk.store.asof import AsOf
from evarisk.store.raw import RawStore
from evarisk.types import Provenance, Window

CFG = load_conjunction_config()
HTML = Path("tests/fixtures/socrates/table_iss_2026-09-17.html").read_bytes()
REPORT = parse_socrates(HTML.decode("utf-8", errors="replace"))
COMPUTED = datetime(2026, 9, 17, 22, 14, 23, tzinfo=UTC)
TCA_1 = datetime(2026, 9, 21, 21, 20, 21, 992000, tzinfo=UTC)
TCA_2 = datetime(2026, 9, 24, 4, 59, 21, 752000, tzinfo=UTC)
TCA_3 = datetime(2026, 9, 23, 7, 34, 27, 941000, tzinfo=UTC)
MARGIN = timedelta(minutes=30)


def seed(tmp_path: Path) -> RawStore:
    store = RawStore(tmp_path)
    store.put(
        source="socrates",
        product="conjunctions_iss",
        url="https://celestrak.org/SOCRATES/table-socrates.php?NAME=ISS%20(ZARYA)&ORDER=MINRANGE&MAX=100",
        content=HTML,
        fetched_at=datetime(2026, 9, 17, 22, 30, tzinfo=UTC),
        published_at=COMPUTED,
        published_at_kind="exact",
        content_type="text/html",
        data_version=f"run {COMPUTED.isoformat()}",
    )
    return store


def trajectory(window: Window) -> Trajectory:
    n = int(window.duration / timedelta(minutes=1)) + 1
    times = [window.start + i * timedelta(minutes=1) for i in range(n)]
    zeros = np.zeros(n, dtype=np.float64)
    return Trajectory(
        provider="oem",
        provenance=Provenance(
            raw_id="oem-test",
            source="nasa_oem",
            product="oem",
            published_at=datetime(2026, 9, 17, tzinfo=UTC),
            published_at_kind="exact",
            data_version="oem 2026-09-17",
            url="https://x.invalid/oem",
        ),
        epoch=datetime(2026, 9, 17, tzinfo=UTC),
        is_reconstruction=False,
        times=times,
        r_km=np.zeros((n, 3), dtype=np.float64),
        lat_deg=zeros,
        lon_deg=zeros,
        alt_km=zeros,
        sunlit=np.ones(n, dtype=bool),
        mlat_deg=zeros,
        l_shell=zeros,
        b_total_nt=zeros,
        in_saa=np.zeros(n, dtype=bool),
        limitations=(),
    )


def test_config_from_socrates_threshold_and_our_margin() -> None:
    assert CFG.margin_minutes == 30.0 and CFG.close_range_km == REPORT.threshold_km == 5.0
    assert {"socrates", "margin", "maxprob", "thresholds", "coorbiting"} <= set(CFG.sources)
    # Пороги правил полёта МКС: жёлтый 1 на 100 000, красный 1 на 10 000; до сверки человеком помечены несверенными.
    assert CFG.yellow_probability == 1e-5 and CFG.red_probability == 1e-4 and CFG.thresholds_verified is False


def test_fixture_tca_and_levels() -> None:
    assert [c.tca for c in REPORT.conjunctions] == [TCA_1, TCA_2, TCA_3]
    # MAXPROB это верхняя оценка Pc (D53): 3.045e-05 не исключает жёлтый порог 1e-5, это elevated;
    # 9.119e-06 и 8.800e-06 ниже жёлтого порога, значит Pc тем более ниже, это none.
    assert [conjunction_level(c, CFG) for c in REPORT.conjunctions] == ["elevated", "none", "none"]
    red = replace(REPORT.conjunctions[0], max_probability=1.05e-4)
    assert conjunction_level(red, CFG) == "high"


def test_docked_vehicle_rows_are_not_conjunctions() -> None:
    # Пристыкованный корабль попадает в отчёт на каждом витке: дистанция сотни метров, относительная скорость
    # метры в секунду, MAXPROB 1.66e-02. Объекты на пересекающихся орбитах в фикстуре идут 9.4–15.3 км/с.
    docked = replace(
        REPORT.conjunctions[0],
        secondary_name="SOYUZ-MS 29",
        min_range_km=0.125,
        rel_speed_kms=0.003,
        max_probability=1.66e-2,
    )
    assert is_coorbiting(docked, CFG) and not any(is_coorbiting(c, CFG) for c in REPORT.conjunctions)
    window = Window(start=docked.tca - MARGIN, end=docked.tca + MARGIN)
    report = replace(REPORT, conjunctions=type(REPORT.conjunctions)((docked, *REPORT.conjunctions[1:])))
    assert conjunctions_touching(report, window, CFG) == []


def test_window_over_first_tca_gives_high_interval_with_socrates_numbers(tmp_path: Path) -> None:
    store = seed(tmp_path)
    window = Window(start=datetime(2026, 9, 21, 21, 0, tzinfo=UTC), end=datetime(2026, 9, 21, 23, 0, tzinfo=UTC))
    result = ConjunctionLine(CFG).assess(window, trajectory(window), AsOf(store, None))
    assert result.line == "conjunctions" and result.mechanism == "planning_conflict"
    assert result.availability == "available" and result.confidence.grade == "medium"
    assert [i.level for i in result.intervals] == ["elevated", "none"]
    high, clear = result.intervals
    # TCA − 30 мин раньше начала окна, интервал обрезан по окну; TCA + 30 мин = 21:50:21.992.
    assert high.start == window.start and high.end == TCA_1 + MARGIN
    assert clear.start == TCA_1 + MARGIN and clear.end == window.end
    assert all(i.kind == "external_forecast" for i in result.intervals)
    rec = store.records(source="socrates")[0]
    assert all(len(i.provenance) == 1 and i.provenance[0].raw_id == rec.id for i in result.intervals)
    for text in (
        "TCA 2026-09-21T21:20:21Z",
        "1.732 км",
        "15.319 км/с",
        "MAXPROB 3.045e-05",
        "верхняя оценка Pc",
        "SMDC ONE 2.4",
    ):
        assert text in high.rule, text
    assert "SMDC ONE" not in clear.rule
    assert result.metrics == {"conjunctions_in_window": 1.0, "coorbiting_rows_in_report": 0.0, "min_range_km": 1.732}
    assert LIMIT_OPERATIONAL in result.limitations and "консервативная оценка, не гарантия" in " ".join(
        result.limitations
    )
    assert any("ждут сверки человеком" in lim for lim in result.limitations)


def test_window_without_conjunctions_is_clear_not_unknown(tmp_path: Path) -> None:
    store = seed(tmp_path)
    window = Window(start=datetime(2026, 9, 19, 0, 0, tzinfo=UTC), end=datetime(2026, 9, 19, 6, 0, tzinfo=UTC))
    result = ConjunctionLine(CFG).assess(window, trajectory(window), AsOf(store, None))
    assert result.availability == "available"
    assert [(i.start, i.end, i.level) for i in result.intervals] == [(window.start, window.end, "none")]
    assert result.metrics == {"conjunctions_in_window": 0.0, "coorbiting_rows_in_report": 0.0}


def test_margin_touching_window_edge_counts(tmp_path: Path) -> None:
    store = seed(tmp_path)
    window = Window(start=datetime(2026, 9, 23, 6, 0, tzinfo=UTC), end=datetime(2026, 9, 23, 7, 30, tzinfo=UTC))
    found = conjunctions_touching(REPORT, window, CFG)
    assert [c.tca for c in found] == [TCA_3]
    result = ConjunctionLine(CFG).assess(window, trajectory(window), AsOf(store, None))
    # MAXPROB 8.800e-06 ниже жёлтого порога: уровень none, но сближение названо в заметке интервала.
    assert [i.level for i in result.intervals] == ["none", "none"]
    assert "OBJECT N" in result.intervals[1].note and "ниже жёлтого порога" in result.intervals[1].note
    assert result.intervals[1].start == TCA_3 - MARGIN and result.intervals[1].end == window.end
    assert result.metrics["min_range_km"] == 4.979


def test_window_beyond_socrates_horizon_is_missing_with_unknown(tmp_path: Path) -> None:
    store = seed(tmp_path)
    window = Window(start=datetime(2026, 9, 26, 0, 0, tzinfo=UTC), end=datetime(2026, 9, 26, 6, 0, tzinfo=UTC))
    result = ConjunctionLine(CFG).assess(window, trajectory(window), AsOf(store, None))
    assert result.availability == "missing"
    assert [i.level for i in result.intervals] == ["unknown"]
    assert any("вне интервала расчёта SOCRATES" in lim for lim in result.limitations)


def test_window_crossing_horizon_marks_tail_unknown(tmp_path: Path) -> None:
    store = seed(tmp_path)
    window = Window(start=datetime(2026, 9, 24, 21, 0, tzinfo=UTC), end=datetime(2026, 9, 24, 23, 0, tzinfo=UTC))
    result = ConjunctionLine(CFG).assess(window, trajectory(window), AsOf(store, None))
    assert result.availability == "available"
    assert [(i.level, i.start, i.end) for i in result.intervals] == [
        ("none", window.start, REPORT.interval_stop),
        ("unknown", REPORT.interval_stop, window.end),
    ]


def test_replay_is_unusable_and_never_quiet(tmp_path: Path) -> None:
    store = seed(tmp_path)
    window = Window(start=datetime(2026, 9, 21, 21, 0, tzinfo=UTC), end=datetime(2026, 9, 21, 23, 0, tzinfo=UTC))
    for cutoff in (datetime(2026, 9, 17, 23, 0, tzinfo=UTC), datetime(2024, 5, 11, 4, 0, tzinfo=UTC)):
        result = ConjunctionLine(CFG).assess(window, trajectory(window), AsOf(store, cutoff))
        assert result.availability == "unusable_for_replay"
        assert result.intervals == () and not any(i.level == "none" for i in result.intervals)
        assert LIMIT_NO_ARCHIVE in result.limitations and result.confidence.grade == "low"


def test_no_report_in_current_mode_is_missing(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    window = Window(start=datetime(2026, 9, 21, 21, 0, tzinfo=UTC), end=datetime(2026, 9, 21, 23, 0, tzinfo=UTC))
    result = ConjunctionLine(CFG).assess(window, trajectory(window), AsOf(store, None))
    assert result.availability == "missing" and result.intervals == ()
    assert any("нет отчёта SOCRATES" in lim for lim in result.limitations)


def test_stale_report_is_stale_not_clear(tmp_path: Path) -> None:
    # Отчёт фикстуры рассчитан 17 сентября 2026 22:14:23 UTC. Запрос 19 сентября 00:00 это давность 25.8 ч, больше
    # 24 ч: окно без сближений не выдаётся за свободное, линия stale с уровнем unknown.
    store = seed(tmp_path)
    window = Window(start=datetime(2026, 9, 19, 0, 0, tzinfo=UTC), end=datetime(2026, 9, 19, 6, 0, tzinfo=UTC))
    late = ConjunctionLine(CFG, now=datetime(2026, 9, 19, 0, 0, tzinfo=UTC))
    result = late.assess(window, trajectory(window), AsOf(store, None))
    assert result.availability == "stale" and [i.level for i in result.intervals] == ["unknown"]
    assert result.metrics["report_age_hours"] == pytest.approx(25.76, abs=0.01)
    assert any("давность 25.8 ч больше 24 ч" in lim for lim in result.limitations)
    fresh = ConjunctionLine(CFG, now=datetime(2026, 9, 18, 12, 0, tzinfo=UTC))
    assert fresh.assess(window, trajectory(window), AsOf(store, None)).availability == "available"
    assert CFG.max_report_age_hours == 24.0 and "report_age" in CFG.sources
