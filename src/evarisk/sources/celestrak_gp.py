from datetime import date, timedelta

from evarisk.sources.base import save
from evarisk.sources.http import HttpFetcher
from evarisk.store.raw import RawRecord, RawStore

GP_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=JSON"
NOTES = "CelesTrak не публикует время создания набора; давность считается от EPOCH"


class CelestrakGpAdapter:
    name = "celestrak"
    products: tuple[str, ...] = ("gp",)
    cadence = timedelta(hours=2)
    supports_archive = False

    def fetch_current(self, fetcher: HttpFetcher, store: RawStore) -> list[RawRecord]:
        res = fetcher.get(GP_URL)
        rec = save(
            store,
            source=self.name,
            product="gp",
            result=res,
            published_at=res.fetched_at,
            published_at_kind="derived",
            data_version="gp latest",
            notes=NOTES,
        )
        return [rec]

    def fetch_archive(self, fetcher: HttpFetcher, store: RawStore, start: date, end: date) -> list[RawRecord]:
        return []
