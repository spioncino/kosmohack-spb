import signal
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from evarisk.config import Settings
from evarisk.db.engine import init_schema, make_engine
from evarisk.db.jobs import JobsRepo
from evarisk.db.source_state import WORKER_SOURCE, SourceStateRepo
from evarisk.sources.http import FetchError, HttpFetcher
from evarisk.store.raw import RawRecord, RawStore
from evarisk.worker.loop import run_forever, run_once

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
def repo() -> SourceStateRepo:
    engine = make_engine("sqlite:///:memory:")
    init_schema(engine)
    return SourceStateRepo(engine)


@pytest.fixture
def store(tmp_path: Path) -> RawStore:
    return RawStore(tmp_path)


@pytest.fixture
def fetcher() -> HttpFetcher:
    return HttpFetcher(httpx.Client())


def test_active_adapter_without_success_is_fetched(
    repo: SourceStateRepo, store: RawStore, fetcher: HttpFetcher
) -> None:
    good = Good()
    outcome = run_once(T0, store, fetcher, {"good": good}, repo)
    assert outcome == {"good": "fetched 1"} and good.calls == 1
    st = repo.get("good")
    assert st.last_success_at == T0 and st.last_attempt_at == T0 and st.last_error is None
    assert len(store.records("good", "p")) == 1


def test_recent_success_is_skipped_until_cadence_passes(
    repo: SourceStateRepo, store: RawStore, fetcher: HttpFetcher
) -> None:
    good = Good()
    repo.mark_success("good", T0 - timedelta(minutes=30))
    assert run_once(T0, store, fetcher, {"good": good}, repo) == {"good": "skipped: not due"}
    assert good.calls == 0
    assert run_once(T0 + timedelta(minutes=30), store, fetcher, {"good": good}, repo) == {"good": "fetched 1"}
    assert good.calls == 1 and repo.get("good").last_success_at == T0 + timedelta(minutes=30)


def test_frozen_and_disabled_are_skipped(repo: SourceStateRepo, store: RawStore, fetcher: HttpFetcher) -> None:
    frozen, disabled = Good(), Good()
    frozen.name, disabled.name = "frozen_src", "disabled_src"
    repo.set_state("frozen_src", "frozen", "manual freeze", T0)
    repo.set_state("disabled_src", "disabled", "no use", T0)
    outcome = run_once(T0, store, fetcher, {"frozen_src": frozen, "disabled_src": disabled}, repo)
    assert outcome == {"frozen_src": "skipped: frozen", "disabled_src": "skipped: disabled"}
    assert frozen.calls == 0 and disabled.calls == 0
    assert repo.get("frozen_src").last_attempt_at is None


def test_failing_adapter_is_recorded_and_others_run(
    repo: SourceStateRepo, store: RawStore, fetcher: HttpFetcher, capsys: pytest.CaptureFixture[str]
) -> None:
    bad, good = Bad(), Good()
    outcome = run_once(T0, store, fetcher, {"bad": bad, "good": good}, repo)
    assert outcome["good"] == "fetched 1" and good.calls == 1
    assert outcome["bad"].startswith("error: FetchError") and "503" in outcome["bad"]
    st = repo.get("bad")
    assert st.last_error is not None and "503" in st.last_error
    assert st.last_attempt_at == T0 and st.last_success_at is None
    assert "bad: error: FetchError" in capsys.readouterr().out


def test_retry_after_error_on_next_run(repo: SourceStateRepo, store: RawStore, fetcher: HttpFetcher) -> None:
    bad = Bad()
    run_once(T0, store, fetcher, {"bad": bad}, repo)
    later = T0 + timedelta(minutes=1)
    outcome = run_once(later, store, fetcher, {"bad": bad}, repo)
    assert bad.calls == 2 and outcome["bad"].startswith("error:")
    assert repo.get("bad").last_attempt_at == later


def test_unexpected_exception_propagates(repo: SourceStateRepo, store: RawStore, fetcher: HttpFetcher) -> None:
    with pytest.raises(RuntimeError):
        run_once(T0, store, fetcher, {"broken": Broken()}, repo)


def test_naive_now_rejected(repo: SourceStateRepo, store: RawStore, fetcher: HttpFetcher) -> None:
    with pytest.raises(ValueError):
        run_once(datetime(2026, 9, 18, 12, 0), store, fetcher, {"good": Good()}, repo)


def test_each_tick_is_marked_in_worker_row(repo: SourceStateRepo, store: RawStore, fetcher: HttpFetcher) -> None:
    assert repo.get(WORKER_SOURCE).last_attempt_at is None
    run_once(T0, store, fetcher, {}, repo)
    assert repo.get(WORKER_SOURCE).last_attempt_at == T0
    run_once(T0 + timedelta(minutes=1), store, fetcher, {"bad": Bad()}, repo)
    assert repo.get(WORKER_SOURCE).last_attempt_at == T0 + timedelta(minutes=1)
    assert repo.get(WORKER_SOURCE).state == "active" and repo.get(WORKER_SOURCE).last_error is None


def test_run_forever_handles_queued_jobs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    url = f"sqlite:///{tmp_path}/evarisk.db"
    engine = make_engine(url)
    init_schema(engine)
    job = JobsRepo(engine).create("refresh", {"source": "good"}, T0)
    good = Good()

    def sleep(seconds: float) -> None:
        signal.raise_signal(signal.SIGTERM)

    settings = Settings(database_url=url, raw_root=tmp_path / "raw", worker_tick_s=5.0)
    run_forever(settings, registry={"good": good}, sleep=sleep)
    assert good.calls == 2
    done = JobsRepo(engine).get(job.id)
    assert done is not None and done.status == "done"
    assert "jobs handled=1" in capsys.readouterr().out


def test_run_forever_stops_on_sigterm(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = Good()
    pauses: list[float] = []

    def sleep(seconds: float) -> None:
        pauses.append(seconds)
        signal.raise_signal(signal.SIGTERM)

    settings = Settings(database_url="sqlite:///:memory:", raw_root=tmp_path, worker_tick_s=5.0)
    before = signal.getsignal(signal.SIGTERM)
    run_forever(settings, registry={"good": good}, sleep=sleep)
    assert good.calls == 1 and pauses == [1.0]
    assert signal.getsignal(signal.SIGTERM) is before
    assert "good=fetched 1" in capsys.readouterr().out


def test_jobs_are_polled_inside_the_pause_between_ticks(tmp_path: Path) -> None:
    # Тик 5 с шагами по 1 с: очередь опрашивается после тика и после каждого шага паузы, пока не пришёл SIGTERM.
    polls: list[int] = []
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 3:
            signal.raise_signal(signal.SIGTERM)

    settings = Settings(database_url="sqlite:///:memory:", raw_root=tmp_path, worker_tick_s=5.0)
    run_forever(settings, registry={}, sleep=sleep, run_jobs=lambda: polls.append(1))
    assert sleeps == [1.0, 1.0, 1.0]
    assert len(polls) == 3
