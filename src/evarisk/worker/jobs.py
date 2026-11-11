import traceback
from collections.abc import Callable
from datetime import datetime, timedelta

from evarisk.api.schemas import AssessmentRequest
from evarisk.api.service import PROGRESS_STEPS, NoTrajectoryData, run_assessment
from evarisk.config import Settings
from evarisk.db.jobs import Job, JobsRepo
from evarisk.db.source_state import SourceStateRepo
from evarisk.sources.archive_mirror import Fetch, http_fetch
from evarisk.sources.base import SourceAdapter
from evarisk.sources.http import FetchError, HttpFetcher
from evarisk.sources.registry import adapters
from evarisk.store.backfill import BackfillReport, backfill
from evarisk.store.raw import RawStore
from evarisk.timeutil import ensure_utc, utc_now
from evarisk.types import Window

STALE_AFTER = timedelta(minutes=10)
SOURCE_ERRORS = (FetchError, ValueError, OSError)


ARCHIVE_STEP = "archive"
ARCHIVE_STARTED = 0.02


def _backfill_archive(
    req: AssessmentRequest, store: RawStore, settings: Settings, now: datetime, archive_fetch: Fetch | None
) -> BackfillReport | None:
    """Докачка архива на период запроса (D59): только «разбор» и «прогноз из прошлого» при включённой настройке."""
    if req.mode == "current" or not settings.archive_backfill:
        return None
    span = Window(start=req.search_start, end=req.search_end)
    return backfill(store, settings.mirror_root, span, req.cutoff, now, fetch=archive_fetch or http_fetch)


def _run_assessment(
    job: Job,
    repo: JobsRepo,
    store: RawStore,
    settings: Settings,
    clock: Callable[[], datetime],
    disabled: frozenset[str],
    archive_fetch: Fetch | None,
) -> str:
    req = AssessmentRequest.model_validate(job.params)

    def on_progress(step: str, fraction: float) -> None:
        repo.progress(job.id, step, fraction, clock())

    on_progress(ARCHIVE_STEP, ARCHIVE_STARTED)
    report = _backfill_archive(req, store, settings, clock(), archive_fetch)
    on_progress(ARCHIVE_STEP, PROGRESS_STEPS[ARCHIVE_STEP])
    notes = () if report is None else tuple(report.notes())
    try:
        result = run_assessment(
            req, store, settings, on_progress=on_progress, disabled=disabled, extra_limitations=notes
        )
    except NoTrajectoryData as exc:
        if report is None:
            raise
        raise NoTrajectoryData(f"{exc}; {'; '.join(notes)}") from exc
    return result.id


def _run_refresh(
    job: Job,
    repo: JobsRepo,
    store: RawStore,
    settings: Settings,
    registry: dict[str, SourceAdapter],
    states: SourceStateRepo,
    fetcher: HttpFetcher | None,
    clock: Callable[[], datetime],
) -> None:
    name = str(job.params.get("source", ""))
    adapter = registry.get(name)
    if adapter is None:
        raise ValueError(f"неизвестный источник {name!r}")
    state = states.get(name)
    if state.state != "active":
        raise ValueError(f"источник {name} в состоянии {state.state}: обновление пропущено, сначала активировать")
    active_fetcher = fetcher or HttpFetcher(timeout_s=settings.http_timeout_s, user_agent=settings.user_agent)
    states.mark_attempt(name, clock())
    try:
        records = adapter.fetch_current(active_fetcher, store)
    except SOURCE_ERRORS as exc:
        states.mark_error(name, f"{type(exc).__name__}: {exc}", clock())
        raise
    states.mark_success(name, clock())
    repo.progress(job.id, f"fetched {len(records)}", 1.0, clock())


def run_pending(
    repo: JobsRepo,
    store: RawStore,
    settings: Settings,
    now: datetime,
    *,
    registry: dict[str, SourceAdapter] | None = None,
    states: SourceStateRepo | None = None,
    fetcher: HttpFetcher | None = None,
    archive_fetch: Fetch | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> int:
    now = ensure_utc(now)
    repo.requeue_stale(now, STALE_AFTER)
    active_registry = adapters() if registry is None else registry
    active_states = SourceStateRepo(repo.engine) if states is None else states
    handled = 0
    while (job := repo.claim(now)) is not None:
        handled += 1
        try:
            if job.kind == "assessment":
                assessment_id: str | None = _run_assessment(
                    job, repo, store, settings, clock, active_states.disabled_sources(), archive_fetch
                )
            elif job.kind == "refresh":
                _run_refresh(job, repo, store, settings, active_registry, active_states, fetcher, clock)
                assessment_id = None
            else:
                raise ValueError(f"неизвестный вид задания {job.kind!r}")
        except Exception as exc:
            traceback.print_exc()
            repo.fail(job.id, f"{type(exc).__name__}: {exc}", clock())
            continue
        repo.finish(job.id, assessment_id, clock())
    return handled
