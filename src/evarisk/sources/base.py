from datetime import date, datetime, timedelta
from typing import Protocol

from evarisk.sources.http import FetchResult, HttpFetcher
from evarisk.store.raw import RawRecord, RawStore
from evarisk.types import PublishedAtKind


class SourceAdapter(Protocol):
    name: str
    products: tuple[str, ...]
    cadence: timedelta
    supports_archive: bool

    def fetch_current(self, fetcher: HttpFetcher, store: RawStore) -> list[RawRecord]: ...

    def fetch_archive(self, fetcher: HttpFetcher, store: RawStore, start: date, end: date) -> list[RawRecord]: ...


def save(
    store: RawStore,
    *,
    source: str,
    product: str,
    result: FetchResult,
    published_at: datetime | None,
    published_at_kind: PublishedAtKind,
    data_version: str,
    notes: str = "",
) -> RawRecord:
    record, _ = store.put(
        source=source,
        product=product,
        url=result.url,
        content=result.content,
        fetched_at=result.fetched_at,
        published_at=published_at,
        published_at_kind=published_at_kind,
        content_type=result.content_type,
        data_version=data_version,
        http_status=result.status,
        notes=notes,
    )
    return record
