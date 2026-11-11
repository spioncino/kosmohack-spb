from datetime import date, timedelta

from evarisk.parsers.socrates import data_as_of
from evarisk.sources.base import save
from evarisk.sources.http import HttpFetcher
from evarisk.store.raw import RawRecord, RawStore

URL = "https://celestrak.org/SOCRATES/table-socrates.php?NAME=ISS%20(ZARYA)&ORDER=MINRANGE&MAX=100"


class SocratesAdapter:
    name = "socrates"
    products: tuple[str, ...] = ("conjunctions_iss",)
    cadence = timedelta(hours=6)
    supports_archive = False

    def fetch_current(self, fetcher: HttpFetcher, store: RawStore) -> list[RawRecord]:
        res = fetcher.get(URL)
        computed = data_as_of(res.content.decode(errors="replace"))
        return [
            save(
                store,
                source=self.name,
                product="conjunctions_iss",
                result=res,
                published_at=computed,
                published_at_kind="exact",
                data_version=f"run {computed.isoformat()}",
            )
        ]

    def fetch_archive(self, fetcher: HttpFetcher, store: RawStore, start: date, end: date) -> list[RawRecord]:
        return []
