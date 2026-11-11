from dataclasses import dataclass
from datetime import datetime

from evarisk.store.raw import RawRecord, RawStore


def availability_key(record: RawRecord) -> tuple[datetime, datetime]:
    fallback = datetime.min.replace(tzinfo=record.fetched_at.tzinfo)
    return (record.published_at or fallback, record.fetched_at)


@dataclass(frozen=True)
class UnreadableRecord:
    """Запись, которую разбор отверг: она пропущена, причина сохранена для ограничений результата."""

    raw_id: str
    source: str
    product: str
    available_at: datetime | None
    reason: str


class AsOf:
    def __init__(self, store: RawStore, t: datetime | None) -> None:
        self._store = store
        self.t = t
        self.unreadable: dict[str, UnreadableRecord] = {}

    def records(self, source: str, product: str | None = None) -> list[RawRecord]:
        found = self._store.records(source=source, product=product)
        if self.t is not None:
            found = [r for r in found if r.published_at is not None and r.published_at <= self.t]
        return sorted(found, key=availability_key)

    def aggregates(self, source: str, product: str | None = None) -> list[RawRecord]:
        found = [r for r in self._store.records(source=source, product=product) if r.published_at is not None]
        return sorted(found, key=availability_key)

    def latest(self, source: str, product: str) -> RawRecord | None:
        found = self.records(source, product)
        return found[-1] if found else None

    def read(self, record: RawRecord) -> bytes:
        return self._store.read(record.id)

    def mark_unreadable(self, record: RawRecord, reason: str) -> None:
        """Отметка о записи, которую не удалось разобрать: первая причина по записи сохраняется."""
        self.unreadable.setdefault(
            record.id,
            UnreadableRecord(
                raw_id=record.id,
                source=record.source,
                product=record.product,
                available_at=record.published_at,
                reason=reason,
            ),
        )
