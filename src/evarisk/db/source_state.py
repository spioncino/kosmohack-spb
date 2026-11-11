from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import Engine, RowMapping, insert, select, update

from evarisk.db.schema import source_state
from evarisk.timeutil import ensure_utc

State = Literal["active", "frozen", "disabled"]
STATES: tuple[State, ...] = ("active", "frozen", "disabled")
WORKER_SOURCE = "_worker"


@dataclass(frozen=True)
class SourceState:
    source: str
    state: State
    changed_at: datetime | None = None
    reason: str | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None


def _check_state(state: str) -> State:
    if state not in STATES:
        raise ValueError(f"неизвестное состояние источника {state!r}, допустимы {STATES}")
    return state


def _from_row(row: RowMapping) -> SourceState:
    return SourceState(
        source=str(row["source"]),
        state=_check_state(str(row["state"])),
        changed_at=row["changed_at"],
        reason=row["reason"],
        last_attempt_at=row["last_attempt_at"],
        last_success_at=row["last_success_at"],
        last_error=row["last_error"],
    )


class SourceStateRepo:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, source: str) -> SourceState:
        with self._engine.connect() as conn:
            row = conn.execute(select(source_state).where(source_state.c.source == source)).mappings().first()
        if row is None:
            return SourceState(source=source, state="active")
        return _from_row(row)

    def all(self) -> list[SourceState]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(source_state).order_by(source_state.c.source)).mappings().all()
        return [_from_row(r) for r in rows]

    def disabled_sources(self) -> frozenset[str]:
        """Источники в состоянии `disabled`: их записи исключаются из расчётов (`RawStore.without`)."""
        return frozenset(s.source for s in self.all() if s.state == "disabled")

    def set_state(self, source: str, state: State, reason: str, now: datetime) -> None:
        self._merge(source, {"state": _check_state(state), "reason": reason, "changed_at": ensure_utc(now)})

    def mark_attempt(self, source: str, now: datetime) -> None:
        self._merge(source, {"last_attempt_at": ensure_utc(now)})

    def mark_success(self, source: str, now: datetime) -> None:
        stamp = ensure_utc(now)
        self._merge(source, {"last_attempt_at": stamp, "last_success_at": stamp, "last_error": None})

    def mark_error(self, source: str, error: str, now: datetime) -> None:
        self._merge(source, {"last_attempt_at": ensure_utc(now), "last_error": error})

    def _merge(self, source: str, values: dict[str, object]) -> None:
        with self._engine.begin() as conn:
            updated = conn.execute(update(source_state).where(source_state.c.source == source).values(values))
            if updated.rowcount == 0:
                conn.execute(insert(source_state).values({"source": source, "state": "active"} | values))
