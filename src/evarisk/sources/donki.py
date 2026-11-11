from datetime import date, timedelta

from evarisk.parsers.donki import parse_notifications
from evarisk.sources.base import save
from evarisk.sources.http import HttpFetcher
from evarisk.store.raw import RawRecord, RawStore
from evarisk.timeutil import utc_now

BASE = "https://kauai.ccmc.gsfc.nasa.gov/DONKI/WS/get/notifications"
CHUNK_DAYS = 7
DATA_VERSION = "notifications"
NOTES = "агрегат; messageIssueTime каждого сообщения внутри"


class DonkiAdapter:
    name = "donki"
    products: tuple[str, ...] = ("notifications",)
    cadence = timedelta(hours=1)
    supports_archive = True

    def _fetch_range(self, fetcher: HttpFetcher, store: RawStore, start: date, end: date) -> RawRecord:
        res = fetcher.get(BASE, params={"startDate": start.isoformat(), "endDate": end.isoformat(), "type": "all"})
        notes = parse_notifications(res.content)
        if not notes:
            return save(
                store,
                source=self.name,
                product="notifications",
                result=res,
                published_at=None,
                published_at_kind="unknown",
                data_version=DATA_VERSION,
                notes="пустой ответ",
            )
        return save(
            store,
            source=self.name,
            product="notifications",
            result=res,
            published_at=max(n.issued_at for n in notes),
            published_at_kind="derived",
            data_version=DATA_VERSION,
            notes=NOTES,
        )

    def fetch_current(self, fetcher: HttpFetcher, store: RawStore) -> list[RawRecord]:
        today = utc_now().date()
        return [self._fetch_range(fetcher, store, today - timedelta(days=CHUNK_DAYS), today)]

    def fetch_archive(self, fetcher: HttpFetcher, store: RawStore, start: date, end: date) -> list[RawRecord]:
        records: list[RawRecord] = []
        cursor = start
        while cursor <= end:
            stop = min(cursor + timedelta(days=CHUNK_DAYS - 1), end)
            records.append(self._fetch_range(fetcher, store, cursor, stop))
            cursor = stop + timedelta(days=1)
        return records
