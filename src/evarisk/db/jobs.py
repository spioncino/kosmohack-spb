import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import Engine, RowMapping, insert, select, update

from evarisk.db.schema import POSTGRES, jobs
from evarisk.timeutil import ensure_utc

Status = Literal["queued", "running", "done", "failed"]
STATUSES: tuple[Status, ...] = ("queued", "running", "done", "failed")
Kind = Literal["assessment", "refresh"]
KINDS: tuple[Kind, ...] = ("assessment", "refresh")
ID_LENGTH = 12


@dataclass(frozen=True)
class Job:
    id: str
    kind: str
    params: dict[str, Any]
    status: Status
    progress: dict[str, Any] | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    assessment_id: str | None


def _check_status(status: str) -> Status:
    if status not in STATUSES:
        raise ValueError(f"неизвестный статус задания {status!r}, допустимы {STATUSES}")
    return status


def _check_kind(kind: str) -> Kind:
    if kind not in KINDS:
        raise ValueError(f"неизвестный вид задания {kind!r}, допустимы {KINDS}")
    return kind


def _check_fraction(fraction: float) -> float:
    if not 0.0 <= fraction <= 1.0:
        raise ValueError(f"доля выполнения вне [0, 1]: {fraction!r}")
    return float(fraction)


def _progress(step: str, fraction: float, at: datetime, **extra: str) -> dict[str, Any]:
    return {"step": step, "fraction": _check_fraction(fraction), "at": ensure_utc(at).isoformat(), **extra}


def _from_row(row: RowMapping) -> Job:
    return Job(
        id=str(row["id"]),
        kind=str(row["kind"]),
        params=dict(row["params"]),
        status=_check_status(str(row["status"])),
        progress=None if row["progress"] is None else dict(row["progress"]),
        error=row["error"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        assessment_id=row["assessment_id"],
    )


class JobsRepo:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @property
    def engine(self) -> Engine:
        return self._engine

    def create(self, kind: str, params: dict[str, Any], now: datetime) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:ID_LENGTH],
            kind=_check_kind(kind),
            params=dict(params),
            status="queued",
            progress=None,
            error=None,
            created_at=ensure_utc(now),
            started_at=None,
            finished_at=None,
            assessment_id=None,
        )
        with self._engine.begin() as conn:
            conn.execute(insert(jobs).values(**job.__dict__))
        return job

    def get(self, job_id: str) -> Job | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(jobs).where(jobs.c.id == job_id)).mappings().first()
        return None if row is None else _from_row(row)

    def claim(self, now: datetime) -> Job | None:
        stamp = ensure_utc(now)
        queued = jobs.c.status == "queued"
        oldest = select(jobs.c.id).where(queued).order_by(jobs.c.created_at, jobs.c.id).limit(1)
        started = {"status": "running", "started_at": stamp, "progress": _progress("claimed", 0.0, stamp)}
        with self._engine.begin() as conn:
            if self._engine.dialect.name == POSTGRES:
                job_id = conn.execute(oldest.with_for_update(skip_locked=True)).scalar_one_or_none()
                if job_id is None:
                    return None
                conn.execute(update(jobs).where(jobs.c.id == job_id).values(started))
            else:
                claimed = update(jobs).where(queued, jobs.c.id == oldest.scalar_subquery()).values(started)
                job_id = conn.execute(claimed.returning(jobs.c.id)).scalar_one_or_none()
                if job_id is None:
                    return None
            row = conn.execute(select(jobs).where(jobs.c.id == job_id)).mappings().one()
        return _from_row(row)

    def progress(self, job_id: str, step: str, fraction: float, now: datetime) -> None:
        self._set(job_id, {"progress": _progress(step, fraction, now)})

    def finish(self, job_id: str, assessment_id: str | None, now: datetime) -> None:
        stamp = ensure_utc(now)
        self._set(job_id, {"status": "done", "finished_at": stamp, "assessment_id": assessment_id, "error": None})

    def fail(self, job_id: str, error: str, now: datetime) -> None:
        self._set(job_id, {"status": "failed", "finished_at": ensure_utc(now), "error": error})

    def requeue_stale(self, now: datetime, older_than: timedelta) -> int:
        stamp = ensure_utc(now)
        requeued = 0
        with self._engine.begin() as conn:
            running = conn.execute(select(jobs).where(jobs.c.status == "running")).mappings().all()
            for row in running:
                job = _from_row(row)
                if job.started_at is None or stamp - job.started_at < older_than:
                    continue
                previous = "" if job.progress is None else str(job.progress.get("step", ""))
                note = (
                    f"задание выполнялось с {job.started_at.isoformat()} и не завершилось за {older_than}; "
                    f"последний шаг {previous!r}; возвращено в очередь"
                )
                values = {
                    "status": "queued",
                    "started_at": None,
                    "progress": _progress("requeued", 0.0, stamp, note=note),
                }
                conn.execute(update(jobs).where(jobs.c.id == job.id, jobs.c.status == "running").values(values))
                requeued += 1
        return requeued

    def _set(self, job_id: str, values: dict[str, Any]) -> None:
        with self._engine.begin() as conn:
            updated = conn.execute(update(jobs).where(jobs.c.id == job_id).values(values))
        if updated.rowcount == 0:
            raise KeyError(job_id)
