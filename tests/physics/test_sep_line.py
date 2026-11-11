from datetime import UTC, datetime, timedelta
from pathlib import Path

from evarisk.orbit.oem import parse_oem
from evarisk.orbit.trajectory import Trajectory, from_oem
from evarisk.risk.config import load_sep_config
from evarisk.risk.lines.sep import SepLine
from evarisk.store.asof import AsOf
from evarisk.store.raw import RawStore
from evarisk.store.views import provenance_of
from evarisk.types import Window

F = datetime(2026, 9, 18, tzinfo=UTC)
WINDOW = Window(start=datetime(2024, 5, 11, 4, 0, tzinfo=UTC), end=datetime(2024, 5, 11, 10, 0, tzinfo=UTC))


def seed(tmp_path: Path) -> RawStore:
    store = RawStore(tmp_path)
    for day in ("20240510", "20240511"):
        store.put(
            source="goes_sgps",
            product="sgps_l2_avg5m",
            url=f"https://x.invalid/{day}.nc",
            content=Path(f"tests/fixtures/goes/sci_sgps-l2-avg5m_g18_d{day}_v3-0-2.nc").read_bytes(),
            fetched_at=F,
            published_at=datetime(2024, 5, int(day[6:]) + 1, 0, 5, tzinfo=UTC),
            published_at_kind="derived",
            content_type="application/x-netcdf",
            data_version="reprocessed v3-0-2",
        )
    store.put(
        source="gfz_kp",
        product="kp",
        url="https://x.invalid/kp.json",
        content=Path("tests/fixtures/gfz/kp_2024-05-10_11.json").read_bytes(),
        fetched_at=F,
        published_at=datetime(2024, 7, 15, tzinfo=UTC),
        published_at_kind="derived",
        content_type="application/json",
        data_version="definitive",
    )
    store.put(
        source="nasa_oem",
        product="oem",
        url="https://x.invalid/oem.txt",
        content=Path("tests/fixtures/nasa_oem/2024-05-12_ISS.OEM_J2K_EPH.txt").read_bytes(),
        fetched_at=F,
        published_at=datetime(2024, 5, 10, 18, 51, 53, tzinfo=UTC),
        published_at_kind="exact",
        content_type="text/plain",
        data_version="oem 2024-05-10",
    )
    return store


def trajectory(store: RawStore) -> Trajectory:
    rec = store.records(source="nasa_oem")[0]
    eph = parse_oem(store.read(rec.id).decode("utf-8"))
    return from_oem(eph, provenance_of(rec), WINDOW, pad=timedelta(0))


def test_review_mode_sees_the_event(tmp_path: Path) -> None:
    store = seed(tmp_path)
    result = SepLine(load_sep_config()).assess(WINDOW, trajectory(store), AsOf(store, None))
    assert result.availability == "available" and result.line == "sep_protons"
    levels = {i.level for i in result.intervals}
    assert levels & {"elevated", "high", "critical"}
    assert all(i.kind == "computed" and i.provenance for i in result.intervals)
    assert result.metrics["max_p10_pfu"] > 50 and result.metrics["max_p100_pfu"] > 1.0
    assert 0.0 <= result.metrics["access_fraction"] <= 1.0 and result.metrics["saa_minutes"] >= 0.0
    assert result.confidence.grade == "medium"


def test_storm_access_exists_on_2024_05_11(tmp_path: Path) -> None:
    store = seed(tmp_path)
    result = SepLine(load_sep_config()).assess(WINDOW, trajectory(store), AsOf(store, None))
    assert result.metrics["access_minutes"] > 0, "доступ частиц на 11 мая равен нулю: модель сломана (D24)"
    assert any(i.level in {"high", "critical"} for i in result.intervals)
    assert any(i.level == "critical" for i in result.intervals), "p100 = 5 pfu при доступе должен давать critical"


def test_quiet_model_gives_less_access_than_storm_model(tmp_path: Path) -> None:
    store = seed(tmp_path)
    cfg = load_sep_config()
    bare = cfg.model_copy(update={"kp_suppression_deg_per_kp": None, "dst_suppression_deg_per_nt": None})
    storm = SepLine(cfg).assess(WINDOW, trajectory(store), AsOf(store, None))
    quiet = SepLine(bare).assess(WINDOW, trajectory(store), AsOf(store, None))
    assert storm.metrics["access_minutes"] > quiet.metrics["access_minutes"]
    assert quiet.confidence.grade == "low"


def test_replay_before_availability_is_missing_not_safe(tmp_path: Path) -> None:
    store = seed(tmp_path)
    cutoff = datetime(2024, 5, 11, 3, 0, tzinfo=UTC)
    result = SepLine(load_sep_config()).assess(WINDOW, trajectory(store), AsOf(store, cutoff))
    assert result.availability == "missing"
    assert not any(i.level == "none" for i in result.intervals)
    assert result.confidence.grade == "low"
