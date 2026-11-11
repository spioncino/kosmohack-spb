from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Engine

from evarisk.config import Settings
from evarisk.db.engine import init_schema, make_engine
from evarisk.db.jobs import JobsRepo
from evarisk.db.source_state import SourceStateRepo
from evarisk.sources.http import FetchError, HttpFetcher
from evarisk.store.raw import RawRecord, RawStore
from evarisk.worker.jobs import STALE_AFTER, run_pending

T0 = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


class Good:
    name = "good"
    products: tuple[str, ...] = ("p",)
    cadence = timedelta(hours=1)
    supports_archive = False

    def __init__(self) -> None:
        self.calls = 0

    def fetch_current(self, fetcher: HttpFetcher, store: RawStore) -> list[RawRecord]:
        self.calls += 1
        rec, _ = store.put(
            source=self.name,
            product="p",
            url="https://x.invalid/p",
            content=f"call {self.calls}".encode(),
            fetched_at=T0,
            published_at=T0,
            published_at_kind="exact",
            content_type="text/plain",
            data_version="v1",
        )
        return [rec]

    def fetch_archive(self, fetcher: HttpFetcher, store: RawStore, start: date, end: date) -> list[RawRecord]:
        return []


class Bad(Good):
    name = "bad"

    def fetch_current(self, fetcher: HttpFetcher, store: RawStore) -> list[RawRecord]:
        self.calls += 1
        raise FetchError("https://x.invalid", 503, "down")


class Broken(Good):
    name = "broken"

    def fetch_current(self, fetcher: HttpFetcher, store: RawStore) -> list[RawRecord]:
        raise RuntimeError("bug in adapter")


@pytest.fixture
def engine() -> Engine:
    eng = make_engine("sqlite:///:memory:")
    init_schema(eng)
    return eng


@pytest.fixture
def repo(engine: Engine) -> JobsRepo:
    return JobsRepo(engine)


@pytest.fixture
def states(engine: Engine) -> SourceStateRepo:
    return SourceStateRepo(engine)


@pytest.fixture
def store(tmp_path: Path) -> RawStore:
    return RawStore(tmp_path / "raw")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(raw_root=tmp_path / "raw", assessments_root=tmp_path / "assessments")


@pytest.fixture
def fetcher() -> HttpFetcher:
    return HttpFetcher(httpx.Client())


def clock() -> datetime:
    return T0 + timedelta(seconds=30)


def test_refresh_job_fetches_and_marks_source(
    repo: JobsRepo, states: SourceStateRepo, store: RawStore, settings: Settings, fetcher: HttpFetcher
) -> None:
    good = Good()
    job = repo.create("refresh", {"source": "good"}, T0)
    handled = run_pending(repo, store, settings, T0, registry={"good": good}, fetcher=fetcher, clock=clock)
    assert handled == 1 and good.calls == 1
    done = repo.get(job.id)
    assert done is not None and done.status == "done" and done.assessment_id is None
    assert done.started_at == T0 and done.finished_at == clock()
    assert done.progress == {"step": "fetched 1", "fraction": 1.0, "at": "2026-09-18T12:00:30+00:00"}
    st = states.get("good")
    assert st.last_success_at == clock() and st.last_attempt_at == clock() and st.last_error is None
    assert len(store.records("good")) == 1


def test_empty_queue_handles_nothing(repo: JobsRepo, store: RawStore, settings: Settings) -> None:
    assert run_pending(repo, store, settings, T0, registry={}) == 0


def test_refresh_failure_is_recorded_in_job_and_source(
    repo: JobsRepo, states: SourceStateRepo, store: RawStore, settings: Settings, fetcher: HttpFetcher
) -> None:
    job = repo.create("refresh", {"source": "bad"}, T0)
    handled = run_pending(repo, store, settings, T0, registry={"bad": Bad()}, fetcher=fetcher, clock=clock)
    assert handled == 1
    failed = repo.get(job.id)
    assert failed is not None and failed.status == "failed"
    assert failed.error is not None and failed.error.startswith("FetchError:") and "503" in failed.error
    st = states.get("bad")
    assert st.last_error is not None and "503" in st.last_error and st.last_success_at is None


def test_unexpected_exception_fails_job_and_continues(
    repo: JobsRepo, store: RawStore, settings: Settings, fetcher: HttpFetcher, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = repo.create("refresh", {"source": "broken"}, T0)
    good = Good()
    after = repo.create("refresh", {"source": "good"}, T0 + timedelta(seconds=1))
    registry = {"broken": Broken(), "good": good}
    handled = run_pending(repo, store, settings, T0, registry=registry, fetcher=fetcher, clock=clock)
    assert handled == 2 and good.calls == 1
    first = repo.get(broken.id)
    assert first is not None and first.status == "failed" and first.error == "RuntimeError: bug in adapter"
    second = repo.get(after.id)
    assert second is not None and second.status == "done"
    assert "bug in adapter" in capsys.readouterr().err


def test_unknown_source_and_inactive_source_fail_with_reason(
    repo: JobsRepo, states: SourceStateRepo, store: RawStore, settings: Settings, fetcher: HttpFetcher
) -> None:
    unknown = repo.create("refresh", {"source": "nope"}, T0)
    frozen = repo.create("refresh", {"source": "good"}, T0 + timedelta(seconds=1))
    states.set_state("good", "frozen", "проверка поведения", T0)
    good = Good()
    handled = run_pending(repo, store, settings, T0, registry={"good": good}, fetcher=fetcher, clock=clock)
    assert handled == 2 and good.calls == 0
    first = repo.get(unknown.id)
    assert first is not None and first.status == "failed" and "неизвестный источник" in str(first.error)
    second = repo.get(frozen.id)
    assert second is not None and second.status == "failed" and "frozen" in str(second.error)
    assert states.get("good").last_attempt_at is None


def test_assessment_with_invalid_params_fails(repo: JobsRepo, store: RawStore, settings: Settings) -> None:
    job = repo.create("assessment", {"mode": "review"}, T0)
    assert run_pending(repo, store, settings, T0, registry={}, clock=clock) == 1
    failed = repo.get(job.id)
    assert failed is not None and failed.status == "failed"
    assert failed.error is not None and failed.error.startswith("ValidationError:")


def test_stale_running_job_is_requeued_and_run(
    repo: JobsRepo, store: RawStore, settings: Settings, fetcher: HttpFetcher
) -> None:
    good = Good()
    job = repo.create("refresh", {"source": "good"}, T0 - STALE_AFTER - timedelta(minutes=1))
    repo.claim(T0 - STALE_AFTER - timedelta(minutes=1))
    handled = run_pending(repo, store, settings, T0, registry={"good": good}, fetcher=fetcher, clock=clock)
    assert handled == 1 and good.calls == 1
    done = repo.get(job.id)
    assert done is not None and done.status == "done" and done.started_at == T0


def test_naive_now_rejected(repo: JobsRepo, store: RawStore, settings: Settings) -> None:
    with pytest.raises(ValueError):
        run_pending(repo, store, settings, datetime(2026, 9, 18, 12, 0), registry={})
