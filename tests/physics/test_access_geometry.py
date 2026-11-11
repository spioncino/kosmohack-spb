from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from evarisk.orbit.oem import parse_oem
from evarisk.orbit.trajectory import from_oem
from evarisk.risk.config import load_sep_config
from evarisk.risk.lines.access_geometry import AccessGeometryLine
from evarisk.risk.lines.sep import cutoff_latitude_deg, rigidity_gv
from evarisk.store.asof import AsOf
from evarisk.store.raw import RawStore
from evarisk.types import Provenance, Window

OEM = Path("tests/fixtures/nasa_oem/2024-05-12_ISS.OEM_J2K_EPH.txt")
EPH = parse_oem(OEM.read_text(encoding="utf-8"))
PROV = Provenance(
    raw_id="fixture",
    source="nasa_oem",
    product="oem",
    published_at=EPH.header.creation_date,
    published_at_kind="exact",
    data_version="oem fixture",
    url="fixture",
)
DAY = Window(start=datetime(2024, 5, 11, 0, 0, tzinfo=UTC), end=datetime(2024, 5, 12, 0, 0, tzinfo=UTC))


def assess(tmp_path: Path, window: Window = DAY):  # type: ignore[no-untyped-def]
    trajectory = from_oem(EPH, PROV, window, pad=timedelta(0))
    line = AccessGeometryLine(load_sep_config())
    return trajectory, line.assess(window, trajectory, AsOf(RawStore(tmp_path), None))


def test_access_minutes_are_the_minutes_above_the_kp_nine_cutoff(tmp_path: Path) -> None:
    # Широта обрезания 30 MeV при Kp 9: спокойные 63.44° по фиту SAMPEX (D62) минус 9·0.92° = 55.16°.
    cfg = load_sep_config()
    cutoff = cutoff_latitude_deg(rigidity_gv(cfg.suit_threshold_mev, cfg.proton_rest_energy_mev), cfg, kp=9.0)
    assert abs(cutoff - 55.16) < 0.05
    trajectory, result = assess(tmp_path)
    expected = float(np.sum(np.abs(trajectory.mlat_deg) >= cutoff))
    assert result.availability == "available" and result.line == "proton_access_geometry"
    assert result.metrics["access_minutes"] == expected > 0.0
    assert result.metrics["kp"] == 9.0 and abs(result.metrics["cutoff_deg"] - cutoff) < 1e-9
    open_minutes = sum((i.end - i.start).total_seconds() / 60.0 for i in result.intervals if i.level == "elevated")
    assert open_minutes == expected
    assert {i.level for i in result.intervals} == {"none", "elevated"}
    assert all(i.kind == "computed" and i.provenance == (PROV,) for i in result.intervals)


def kp_open(note: str) -> float:
    return float(note.split("открывается с Kp ")[1].split(";")[0].replace(",", "."))


def test_each_pass_names_the_kp_from_which_it_opens(tmp_path: Path) -> None:
    # Проход открывается с Kp = (63.44° − пик |mlat|) / 0.92, не ниже нуля. Пики витков 11 мая по эфемериде:
    # 63.6° в 01:35, 65.6° в 03:09, 65.9° в 04:43, 64.5° в 06:17 выше спокойной широты обрезания, поэтому эти
    # четыре прохода открыты при любом Kp; виток 09:24 с пиком 58.0° открывается с Kp (63.44 − 58.0)/0.92 = 5.9.
    _, result = assess(tmp_path)
    passes = [i for i in result.intervals if i.level == "elevated"]
    assert passes and all("открывается с Kp" in i.note for i in passes)
    assert all(0.0 <= kp_open(i.note) <= 9.0 for i in passes)
    always_open = [i for i in passes if kp_open(i.note) == 0.0]
    assert [f"{i.start:%H}" for i in always_open] == ["01", "03", "04", "06"]
    late = next(i for i in passes if i.start <= datetime(2024, 5, 11, 9, 24, tzinfo=UTC) < i.end)
    assert 5.6 <= kp_open(late.note) <= 6.2


def test_daily_cycle_leaves_whole_six_hour_windows_without_access(tmp_path: Path) -> None:
    # Пики |mlat| витков 11 мая после 09:24 (58.0°) и до 23:12 (58.8°) не выше 54.8°: ниже широты обрезания
    # при Kp 9 (55.16°). Значит с 10:00 до 23:00 трасса закрыта даже при самой сильной буре (суточный цикл).
    _, result = assess(tmp_path)
    passes = [i for i in result.intervals if i.level == "elevated"]
    closed_from = datetime(2024, 5, 11, 10, 0, tzinfo=UTC)
    closed_to = datetime(2024, 5, 11, 23, 0, tzinfo=UTC)
    assert [i for i in passes if i.end <= closed_from]
    assert not [i for i in passes if i.start < closed_to and i.end > closed_from]


def test_line_is_informational_and_says_so(tmp_path: Path) -> None:
    _, result = assess(tmp_path)
    assert any("не прогноз события" in lim for lim in result.limitations)
    assert any("в сравнении окон не участвует" in lim for lim in result.limitations)
