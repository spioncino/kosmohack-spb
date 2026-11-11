import json
from datetime import UTC, date, datetime, timedelta

from evarisk.parsers.swpc_text import issued_at
from evarisk.sources.base import save
from evarisk.sources.http import HttpFetcher
from evarisk.store.raw import RawRecord, RawStore

S = "https://services.swpc.noaa.gov"
PRODUCTS: dict[str, str] = {
    "3day_forecast": f"{S}/text/3-day-forecast.txt",
    "kp_1m": f"{S}/json/planetary_k_index_1m.json",
    "kp_forecast": f"{S}/products/noaa-planetary-k-index-forecast.json",
    "scales": f"{S}/products/noaa-scales.json",
    "alerts": f"{S}/products/alerts.json",
    "protons_7d": f"{S}/json/goes/primary/integral-protons-7-day.json",
    "instrument_sources": f"{S}/json/goes/instrument-sources.json",
}
LIVE_VERSION = "live"
NOTES_ALERTS = "агрегат; issue_datetime каждого сообщения внутри"
NOTES_SNAPSHOT = "снимок; время каждого отсчёта в time_tag"


def latest_alert_issue(content: bytes) -> datetime:
    rows = json.loads(content)
    if not isinstance(rows, list) or not rows:
        raise ValueError("пустой alerts.json")
    stamps = [datetime.strptime(r["issue_datetime"], "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=UTC) for r in rows]
    return max(stamps)


class SwpcLiveAdapter:
    name = "swpc_live"
    products = tuple(PRODUCTS)
    cadence = timedelta(minutes=5)
    supports_archive = False

    def fetch_current(self, fetcher: HttpFetcher, store: RawStore) -> list[RawRecord]:
        records: list[RawRecord] = []
        for product, url in PRODUCTS.items():
            res = fetcher.get(url)
            if product == "3day_forecast":
                issued = issued_at(res.content.decode(errors="replace"))
                rec = save(
                    store,
                    source=self.name,
                    product=product,
                    result=res,
                    published_at=issued,
                    published_at_kind="exact",
                    data_version=f"issue {issued:%H%M}",
                )
            elif product == "alerts":
                rec = save(
                    store,
                    source=self.name,
                    product=product,
                    result=res,
                    published_at=latest_alert_issue(res.content),
                    published_at_kind="derived",
                    data_version=LIVE_VERSION,
                    notes=NOTES_ALERTS,
                )
            else:
                rec = save(
                    store,
                    source=self.name,
                    product=product,
                    result=res,
                    published_at=res.fetched_at,
                    published_at_kind="derived",
                    data_version=LIVE_VERSION,
                    notes=NOTES_SNAPSHOT,
                )
            records.append(rec)
        return records

    def fetch_archive(self, fetcher: HttpFetcher, store: RawStore, start: date, end: date) -> list[RawRecord]:
        return []
