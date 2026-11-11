from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

from evarisk.orbit.oem import OemEphemeris, parse_oem
from evarisk.orbit.trajectory import Trajectory, from_oem
from evarisk.risk.lines.illumination import LIMIT_CONDITION, RULE, IlluminationLine
from evarisk.store.asof import AsOf
from evarisk.store.raw import RawStore
from evarisk.types import LineAssessment, Provenance, Window


def ephemeris(name: str) -> OemEphemeris:
    return parse_oem(Path(f"tests/fixtures/nasa_oem/{name}_ISS.OEM_J2K_EPH.txt").read_text(encoding="utf-8"))


def provenance(eph: OemEphemeris, raw_id: str) -> Provenance:
    return Provenance(
        raw_id=raw_id,
        source="nasa_oem",
        product="oem",
        published_at=eph.header.creation_date,
        published_at_kind="exact",
        data_version="oem fixture",
        url="fixture",
    )


def day(y: int, m: int, d: int) -> Window:
    start = datetime(y, m, d, tzinfo=UTC)
    return Window(start=start, end=start + timedelta(days=1))


def trajectory(name: str, window: Window, *, reconstruction: bool) -> Trajectory:
    eph = ephemeris(name)
    return from_oem(eph, provenance(eph, f"oem-{name}"), window, pad=timedelta(0), is_reconstruction=reconstruction)


def assess(tmp_path: Path, name: str, window: Window, *, reconstruction: bool = True) -> LineAssessment:
    tr = trajectory(name, window, reconstruction=reconstruction)
    return IlluminationLine().assess(window, tr, AsOf(RawStore(tmp_path), None))


def covered_hours(a: LineAssessment) -> float:
    return sum((iv.end - iv.start).total_seconds() for iv in a.intervals) / 3600.0


def test_high_beta_day_is_almost_fully_sunlit(tmp_path: Path) -> None:
    # 11 мая 2024: бета-угол 69.5–69.75° (orbit/README.md) у критического для 415 км arcsin(6371/6786) = 69.9°,
    # поэтому сутки почти без тени: доля света не ниже 0.9, тень не длиннее 10 мин (измерено 0.951 и 6 мин).
    a = assess(tmp_path, "2024-05-12", day(2024, 5, 11))
    assert a.line == "illumination" and a.mechanism == "work_conditions" and a.availability == "available"
    assert a.metrics["sunlit_fraction"] >= 0.9
    assert 0.0 < a.metrics["longest_shadow_minutes"] <= 10.0
    assert a.metrics["starts_in_daylight"] in (0.0, 1.0)
    assert a.limitations == (LIMIT_CONDITION,) and a.confidence.grade == "high"
    assert all(iv.kind == "computed" and iv.rule == RULE for iv in a.intervals)
    assert all(iv.provenance == (a.intervals[0].provenance[0],) for iv in a.intervals)
    assert a.intervals[0].provenance[0].source == "nasa_oem" and a.intervals[0].provenance[0].raw_id == "oem-2024-05-12"
    assert abs(covered_hours(a) - 24.0) < 1e-9


def test_low_beta_day_has_maximal_eclipses(tmp_path: Path) -> None:
    # 21 июня 2024 по эфемериде 18 июня: тень есть. Для цилиндрической тени на высоте около 415 км максимальная
    # (при бета-угле 0) доля тени за виток равна 2·arcsin(6371/6786)/360° = 0.388, при периоде 92.9 мин это
    # 36 мин тени и доля света 0.61. Измерено: доля 0.615, самая длинная тень 36 мин, то есть бета-угол в эти
    # сутки близок к нулю. Границы допускают небольшой бета-угол.
    a = assess(tmp_path, "2024-06-18", day(2024, 6, 21), reconstruction=False)
    assert 0.60 <= a.metrics["sunlit_fraction"] <= 0.70
    assert 30.0 <= a.metrics["longest_shadow_minutes"] <= 38.0
    assert a.confidence.grade == "medium" and "прогноз" in a.confidence.depends_on
    levels = [iv.level for iv in a.intervals]
    assert set(levels) == {"none", "elevated"}
    assert all(x != y for x, y in pairwise(levels)), "интервалы чередуются свет/тень"
    # За сутки около 24·60/92.9 = 15.5 витка, значит 15 или 16 теней.
    assert 15 <= levels.count("elevated") <= 16
    assert all(iv.note == ("свет" if iv.level == "none" else "тень") for iv in a.intervals)
    assert abs(covered_hours(a) - 24.0) < 1e-9


def test_metrics_follow_the_window_not_the_day(tmp_path: Path) -> None:
    # Окно 21 июня 00:00–06:00 по той же эфемериде начинается в тени (00:00 в списке стартов в тени по
    # измерению на сетке эфемериды), поэтому starts_in_daylight равен 0.0 и первый интервал это тень.
    window = Window(start=datetime(2024, 6, 21, tzinfo=UTC), end=datetime(2024, 6, 21, 6, tzinfo=UTC))
    a = assess(tmp_path, "2024-06-18", window, reconstruction=False)
    assert a.metrics["starts_in_daylight"] == 0.0 and a.intervals[0].level == "elevated"
    assert a.intervals[0].start == window.start and a.intervals[-1].end == window.end
    assert abs(covered_hours(a) - 6.0) < 1e-9
    assert 0.55 <= a.metrics["sunlit_fraction"] <= 0.70
