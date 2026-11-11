from datetime import UTC, date, datetime, time, timedelta

from evarisk.parsers.gfz_kp import KpValue, parse_kp_json
from evarisk.sources.base import save
from evarisk.sources.http import HttpFetcher
from evarisk.store.raw import RawRecord, RawStore
from evarisk.timeutil import utc_now

URL = "https://kp.gfz.de/app/json/"
INTERVAL = timedelta(hours=3)
PUBLICATION_DELAY = timedelta(hours=1)
NOTES = "D18: публикация nowcast принята как конец 3-часового интервала + 1 ч; поле status различает def и nowcast"
DEFINITIVE_STATUS = "def"


def data_version(values: list[KpValue]) -> str:
    return "definitive" if all(v.status == DEFINITIVE_STATUS for v in values) else "nowcast"


class GfzKpAdapter:
    name = "gfz_kp"
    products: tuple[str, ...] = ("kp",)
    cadence = timedelta(hours=1)
    supports_archive = True

    def _fetch(self, fetcher: HttpFetcher, store: RawStore, start: datetime, end: datetime) -> RawRecord:
        res = fetcher.get(
            URL,
            params={
                "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "index": "Kp",
            },
        )
        values = parse_kp_json(res.content)
        if not values:
            return save(
                store,
                source=self.name,
                product="kp",
                result=res,
                published_at=None,
                published_at_kind="unknown",
                data_version="empty",
                notes="пустой ответ",
            )
        return save(
            store,
            source=self.name,
            product="kp",
            result=res,
            published_at=max(v.start for v in values) + INTERVAL + PUBLICATION_DELAY,
            published_at_kind="derived",
            data_version=data_version(values),
            notes=NOTES,
        )

    def fetch_current(self, fetcher: HttpFetcher, store: RawStore) -> list[RawRecord]:
        now = utc_now()
        return [self._fetch(fetcher, store, now - timedelta(days=3), now)]

    def fetch_archive(self, fetcher: HttpFetcher, store: RawStore, start: date, end: date) -> list[RawRecord]:
        return [
            self._fetch(
                fetcher,
                store,
                datetime.combine(start, time.min, tzinfo=UTC),
                datetime.combine(end, time(23, 59, 59), tzinfo=UTC),
            )
        ]
