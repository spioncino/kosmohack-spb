from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine

from evarisk.db.engine import init_schema, make_engine
from evarisk.db.jobs import JobsRepo

T0 = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
PARAMS = {"mode": "review", "eva_start": "2024-05-10T14:00:00Z", "duration_hours": 6.0}


@pytest.fixture
def engine() -> Engine:
    eng = make_engine("sqlite:///:memory:")
    init_schema(eng)
    return eng


@pytest.fixture
def repo(engine: Engine) -> JobsRepo:
    return JobsRepo(engine)


def test_create_and_get_round_trip(repo: JobsRepo) -> None:
    job = repo.create("assessment", PARAMS, T0)
    assert len(job.id) == 12 and job.status == "queued" and job.kind == "assessment"
    stored = repo.get(job.id)
    assert stored == job
    assert stored is not None and stored.params == PARAMS and stored.created_at == T0
    assert stored.created_at.utcoffset() == timedelta(0)
    assert stored.progress is None and stored.error is None and stored.assessment_id is None
    assert stored.started_at is None and stored.finished_at is None


def test_unknown_job_is_none_and_updates_raise(repo: JobsRepo) -> None:
    assert repo.get("nope") is None
    with pytest.raises(KeyError):
        repo.progress("nope", "trajectory", 0.2, T0)
    with pytest.raises(KeyError):
        repo.finish("nope", None, T0)
    with pytest.raises(KeyError):
        repo.fail("nope", "x", T0)


def test_claim_takes_oldest_once(repo: JobsRepo) -> None:
    first = repo.create("assessment", PARAMS, T0)
    second = repo.create("refresh", {"source": "nasa_oem"}, T0 + timedelta(seconds=1))
    claimed = repo.claim(T0 + timedelta(minutes=1))
    assert claimed is not None and claimed.id == first.id
    assert claimed.status == "running" and claimed.started_at == T0 + timedelta(minutes=1)
    assert claimed.progress is not None and claimed.progress["step"] == "claimed"
    again = repo.claim(T0 + timedelta(minutes=2))
    assert again is not None and again.id == second.id
    assert repo.claim(T0 + timedelta(minutes=3)) is None
    ids = {first.id, second.id}
    assert {claimed.id, again.id} == ids and claimed.id != again.id


def test_claim_on_empty_queue_is_none(repo: JobsRepo) -> None:
    assert repo.claim(T0) is None


def test_progress_finish_and_fail(repo: JobsRepo) -> None:
    job = repo.create("assessment", PARAMS, T0)
    repo.claim(T0)
    repo.progress(job.id, "line:sep_protons", 0.5, T0 + timedelta(seconds=2))
    running = repo.get(job.id)
    assert running is not None and running.status == "running"
    assert running.progress == {"step": "line:sep_protons", "fraction": 0.5, "at": "2026-09-18T12:00:02+00:00"}
    repo.finish(job.id, "abc123", T0 + timedelta(seconds=5))
    done = repo.get(job.id)
    assert done is not None and done.status == "done" and done.assessment_id == "abc123"
    assert done.finished_at == T0 + timedelta(seconds=5) and done.error is None
    other = repo.create("refresh", {"source": "donki"}, T0)
    repo.claim(T0)
    repo.fail(other.id, "FetchError: 503", T0 + timedelta(seconds=7))
    failed = repo.get(other.id)
    assert failed is not None and failed.status == "failed" and failed.error == "FetchError: 503"
    assert failed.finished_at == T0 + timedelta(seconds=7) and failed.assessment_id is None


def test_fraction_outside_unit_interval_rejected(repo: JobsRepo) -> None:
    job = repo.create("assessment", PARAMS, T0)
    with pytest.raises(ValueError):
        repo.progress(job.id, "windows", 1.5, T0)


def test_unknown_kind_rejected(repo: JobsRepo) -> None:
    with pytest.raises(ValueError):
        repo.create("export", {}, T0)


def test_naive_now_rejected(repo: JobsRepo) -> None:
    with pytest.raises(ValueError):
        repo.create("assessment", PARAMS, datetime(2026, 9, 18, 12, 0))
    repo.create("assessment", PARAMS, T0)
    with pytest.raises(ValueError):
        repo.claim(datetime(2026, 9, 18, 12, 0))


def test_requeue_stale_returns_old_running_jobs_to_queue(repo: JobsRepo) -> None:
    stale = repo.create("assessment", PARAMS, T0)
    fresh = repo.create("assessment", PARAMS, T0 + timedelta(seconds=1))
    repo.claim(T0)
    repo.progress(stale.id, "line:sep_protons", 0.5, T0 + timedelta(minutes=1))
    repo.claim(T0 + timedelta(minutes=8))
    assert repo.requeue_stale(T0 + timedelta(minutes=12), timedelta(minutes=10)) == 1
    back = repo.get(stale.id)
    assert back is not None and back.status == "queued" and back.started_at is None
    assert back.progress is not None and back.progress["step"] == "requeued" and back.progress["fraction"] == 0.0
    assert "line:sep_protons" in back.progress["note"] and "2026-09-18T12:00:00+00:00" in back.progress["note"]
    still = repo.get(fresh.id)
    assert still is not None and still.status == "running"
    reclaimed = repo.claim(T0 + timedelta(minutes=13))
    assert reclaimed is not None and reclaimed.id == stale.id
    assert repo.requeue_stale(T0 + timedelta(minutes=13), timedelta(minutes=10)) == 0


def test_finished_jobs_are_never_requeued(repo: JobsRepo) -> None:
    job = repo.create("assessment", PARAMS, T0)
    repo.claim(T0)
    repo.finish(job.id, "abc", T0 + timedelta(seconds=1))
    assert repo.requeue_stale(T0 + timedelta(hours=1), timedelta(minutes=10)) == 0
    done = repo.get(job.id)
    assert done is not None and done.status == "done"
