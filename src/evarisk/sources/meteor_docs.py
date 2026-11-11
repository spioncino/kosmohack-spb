from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from evarisk.sources.base import save
from evarisk.sources.http import HttpFetcher
from evarisk.store.raw import RawRecord, RawStore

NTRS = "https://ntrs.nasa.gov/api/citations"
FLUX_FILE = "flux_data.txt"


@dataclass(frozen=True)
class MeoForecast:
    """Годовой прогноз метеорных потоков NASA MEO для низкой орбиты: запись NTRS с PDF и почасовыми данными.

    `distributed` это дата распространения записи NTRS (поле `distributionDate`); файлы данных загружены в NTRS
    раньше неё (Last-Modified 2023-10-19, 2024-09-26, 2025-09-24), поэтому доступность это конец этих суток.
    """

    year: int
    citation: str
    pdf: str
    distributed: date

    @property
    def available(self) -> datetime:
        return datetime.combine(self.distributed, time(23, 59), tzinfo=UTC)

    @property
    def pdf_product(self) -> str:
        return f"LEO_Forecast_{self.year}"

    @property
    def flux_product(self) -> str:
        return flux_product(self.year)

    @property
    def pdf_url(self) -> str:
        return f"{NTRS}/{self.citation}/downloads/{self.pdf}"

    @property
    def flux_url(self) -> str:
        return f"{NTRS}/{self.citation}/downloads/{FLUX_FILE}"

    @property
    def version(self) -> str:
        return f"MEO {self.year}, NTRS {self.citation}, distributed {self.distributed.isoformat()}"


# Прогнозы с машиночитаемым файлом flux_data.txt; прогнозы 2019–2023 годов опубликованы только в PDF.
FORECASTS: dict[int, MeoForecast] = {
    2024: MeoForecast(2024, "20230015158", "LEO_Forecast_2024.pdf", date(2023, 11, 2)),
    2025: MeoForecast(2025, "20240012381", "LEO_Forecast_2025.pdf", date(2024, 10, 18)),
    2026: MeoForecast(2026, "20250009524", "LEO_Forecast_2026.pdf", date(2025, 10, 17)),
}
NOTES = "дата распространения записи NTRS; файл загружен раньше неё, доступность принята как конец этих суток"


def flux_product(year: int) -> str:
    return f"meo_flux_{year}"


class MeteorDocsAdapter:
    name = "meteor_docs"
    products = tuple(p for f in FORECASTS.values() for p in (f.pdf_product, f.flux_product))
    cadence = timedelta(days=30)
    supports_archive = False

    def fetch_current(self, fetcher: HttpFetcher, store: RawStore) -> list[RawRecord]:
        records: list[RawRecord] = []
        for forecast in FORECASTS.values():
            for product, url in ((forecast.pdf_product, forecast.pdf_url), (forecast.flux_product, forecast.flux_url)):
                records.append(
                    save(
                        store,
                        source=self.name,
                        product=product,
                        result=fetcher.get(url),
                        published_at=forecast.available,
                        published_at_kind="derived",
                        data_version=forecast.version,
                        notes=NOTES,
                    )
                )
        return records

    def fetch_archive(self, fetcher: HttpFetcher, store: RawStore, start: date, end: date) -> list[RawRecord]:
        return []
