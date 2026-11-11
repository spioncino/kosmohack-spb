import signal
import time
from collections.abc import Callable
from datetime import datetime
from types import FrameType

from evarisk.config import Settings
from evarisk.db.engine import init_schema, make_engine
from evarisk.db.jobs import JobsRepo
from evarisk.db.source_state import WORKER_SOURCE, SourceStateRepo
from evarisk.sources.base import SourceAdapter
from evarisk.sources.http import FetchError, HttpFetcher
from evarisk.sources.registry import adapters
from evarisk.store.raw import RawStore
from evarisk.timeutil import ensure_utc, utc_now
from evarisk.worker.jobs import run_pending

RECORDED_ERRORS = (FetchError, ValueError, OSError)
PAUSE_STEP_S = 1.0


def run_once(
    now: datetime,
    store: RawStore,
    fetcher: HttpFetcher,
    registry: dict[str, SourceAdapter],
    repo: SourceStateRepo,
) -> dict[str, str]:
    now = ensure_utc(now)
    repo.mark_attempt(WORKER_SOURCE, now)
    outcome: dict[str, str] = {}
    for name, adapter in registry.items():
        state = repo.get(name)
        if state.state != "active":
            outcome[name] = f"skipped: {state.state}"
            continue
        if state.last_success_at is not None and now - state.last_success_at < adapter.cadence:
            outcome[name] = "skipped: not due"
            continue
        repo.mark_attempt(name, now)
        try:
            records = adapter.fetch_current(fetcher, store)
        except RECORDED_ERRORS as exc:
            message = f"{type(exc).__name__}: {exc}"
            repo.mark_error(name, message, now)
            outcome[name] = f"error: {message}"
            print(f"{name}: {outcome[name]}", flush=True)
            continue
        repo.mark_success(name, now)
        outcome[name] = f"fetched {len(records)}"
    return outcome


class StopFlag:
    def __init__(self) -> None:
        self.requested = False

    def __call__(self, signum: int, frame: FrameType | None) -> None:
        self.requested = True


def _pause(
    seconds: float, stop: StopFlag, sleep: Callable[[float], None], between: Callable[[], None] | None = None
) -> None:
    """Пауза между тиками шагами по `PAUSE_STEP_S`; `between` вызывается после каждого шага.

    Очередь заданий опрашивается внутри паузы: задание со страницы стартует через секунду, а не через тик.
    """
    remaining = seconds
    while remaining > 0 and not stop.requested:
        step = min(PAUSE_STEP_S, remaining)
        sleep(step)
        remaining -= step
        if between is not None and not stop.requested:
            between()


def run_forever(
    settings: Settings,
    *,
    registry: dict[str, SourceAdapter] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    run_jobs: Callable[[], None] | None = None,
) -> None:
    engine = make_engine(settings.database_url)
    init_schema(engine)
    repo = SourceStateRepo(engine)
    jobs_repo = JobsRepo(engine)
    store = RawStore(settings.raw_root)
    fetcher = HttpFetcher(timeout_s=settings.http_timeout_s, user_agent=settings.user_agent)
    active_registry = adapters() if registry is None else registry

    def run_pending_jobs() -> None:
        handled = run_pending(
            jobs_repo, store, settings, utc_now(), registry=active_registry, states=repo, fetcher=fetcher
        )
        if handled:
            print(f"{utc_now():%Y-%m-%dT%H:%M:%SZ} jobs handled={handled}", flush=True)

    active_run_jobs = run_pending_jobs if run_jobs is None else run_jobs
    stop = StopFlag()
    previous = signal.signal(signal.SIGTERM, stop)
    try:
        while not stop.requested:
            outcome = run_once(utc_now(), store, fetcher, active_registry, repo)
            summary = ", ".join(f"{name}={result}" for name, result in outcome.items())
            print(f"{utc_now():%Y-%m-%dT%H:%M:%SZ} {summary}", flush=True)
            active_run_jobs()
            _pause(settings.worker_tick_s, stop, sleep, active_run_jobs)
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
