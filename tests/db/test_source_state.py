from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine

from evarisk.db.engine import init_schema, make_engine
from evarisk.db.schema import UtcDateTime, jobs, metadata
from evarisk.db.source_state import SourceStateRepo

T0 = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
JOB_COLUMNS = {
    "id",
    "kind",
    "params",
    "status",
    "progress",
    "error",
    "created_at",
    "started_at",
    "finished_at",
    "assessment_id",
}


@pytest.fixture
def engine() -> Engine:
    eng = make_engine("sqlite:///:memory:")
    init_schema(eng)
    return eng


@pytest.fixture
def repo(engine: Engine) -> SourceStateRepo:
    return SourceStateRepo(engine)


def test_unknown_source_is_active_without_row(repo: SourceStateRepo) -> None:
    st = repo.get("nasa_oem")
    assert st.state == "active" and st.changed_at is None and st.reason is None
    assert st.last_attempt_at is None and st.last_success_at is None and st.last_error is None
    assert repo.all() == []


def test_set_state_and_read_back_tz_aware(repo: SourceStateRepo) -> None:
    repo.set_state("celestrak", "frozen", "usage policy", T0)
    st = repo.get("celestrak")
    assert st.state == "frozen" and st.reason == "usage policy"
    assert st.changed_at == T0
    assert st.changed_at is not None and st.changed_at.utcoffset() == timedelta(0)
    assert [s.source for s in repo.all()] == ["celestrak"]
    repo.set_state("celestrak", "active", "policy reviewed", T0 + timedelta(hours=2))
    assert repo.get("celestrak").state == "active"
    assert repo.get("celestrak").changed_at == T0 + timedelta(hours=2)


def test_marks_update_row(repo: SourceStateRepo) -> None:
    repo.mark_attempt("gfz_kp", T0)
    repo.mark_error("gfz_kp", "FetchError: 503", T0)
    st = repo.get("gfz_kp")
    assert st.state == "active" and st.last_attempt_at == T0
    assert st.last_error == "FetchError: 503" and st.last_success_at is None
    later = T0 + timedelta(hours=1)
    repo.mark_success("gfz_kp", later)
    st = repo.get("gfz_kp")
    assert st.last_success_at == later and st.last_attempt_at == later and st.last_error is None


def test_naive_datetime_rejected(repo: SourceStateRepo, engine: Engine) -> None:
    with pytest.raises(ValueError):
        repo.set_state("donki", "disabled", "x", datetime(2026, 9, 18, 12, 0))
    with pytest.raises(ValueError):
        repo.mark_success("donki", datetime(2026, 9, 18, 12, 0))
    with pytest.raises(ValueError):
        UtcDateTime().process_bind_param(datetime(2026, 9, 18, 12, 0), engine.dialect)


def test_invalid_state_rejected(repo: SourceStateRepo) -> None:
    with pytest.raises(ValueError):
        repo.set_state("donki", "paused", "x", T0)  # type: ignore[arg-type]


def test_sqlite_stores_iso_string_with_zone(repo: SourceStateRepo, engine: Engine) -> None:
    repo.set_state("socrates", "disabled", "no operational use", T0)
    with engine.connect() as conn:
        raw = conn.exec_driver_sql("select changed_at from source_state where source = 'socrates'").scalar_one()
    assert raw == "2026-09-18T12:00:00+00:00"


def test_file_engine_creates_parent_directory(tmp_path: Path) -> None:
    eng = make_engine(f"sqlite:///{tmp_path}/nested/evarisk.db")
    init_schema(eng)
    SourceStateRepo(eng).mark_attempt("swpc_live", T0)
    assert (tmp_path / "nested" / "evarisk.db").exists()
    assert SourceStateRepo(eng).get("swpc_live").last_attempt_at == T0


def test_jobs_table_declared() -> None:
    assert {"source_state", "jobs"} <= set(metadata.tables)
    assert set(jobs.c.keys()) == JOB_COLUMNS
