import re
from datetime import date, timedelta

from evarisk.orbit.oem import creation_date, oem_availability
from evarisk.sources.base import save
from evarisk.sources.http import HttpFetcher
from evarisk.store.raw import RawRecord, RawStore

S3 = "https://nasa-public-data.s3.amazonaws.com"
_PREFIX = re.compile(r"<Prefix>iss-coords/(\d{4}-\d{2}-\d{2})/</Prefix>")


def _months(start: date, end: date) -> list[str]:
    result: list[str] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        result.append(f"{y:04d}-{m:02d}-")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return result


class NasaOemAdapter:
    name = "nasa_oem"
    products: tuple[str, ...] = ("oem",)
    cadence = timedelta(hours=6)
    supports_archive = True

    def list_days(self, fetcher: HttpFetcher, month_prefix: str) -> list[date]:
        res = fetcher.get(
            f"{S3}/", params={"prefix": f"iss-coords/{month_prefix}", "delimiter": "/", "max-keys": "1000"}
        )
        return [date.fromisoformat(d) for d in _PREFIX.findall(res.content.decode())]

    def _fetch_one(self, fetcher: HttpFetcher, store: RawStore, folder: str) -> RawRecord:
        res = fetcher.get(f"{S3}/iss-coords/{folder}/ISS_OEM/ISS.OEM_J2K_EPH.txt")
        created = creation_date(res.content)
        available, note = oem_availability(created, res.last_modified)
        return save(
            store,
            source=self.name,
            product="oem",
            result=res,
            published_at=available,
            published_at_kind="exact",
            data_version=f"oem {created.isoformat()}",
            notes=note,
        )

    def fetch_current(self, fetcher: HttpFetcher, store: RawStore) -> list[RawRecord]:
        return [self._fetch_one(fetcher, store, "current")]

    def fetch_archive(self, fetcher: HttpFetcher, store: RawStore, start: date, end: date) -> list[RawRecord]:
        records: list[RawRecord] = []
        for prefix in _months(start, end):
            for day in self.list_days(fetcher, prefix):
                if start <= day <= end:
                    records.append(self._fetch_one(fetcher, store, day.isoformat()))
        return records
