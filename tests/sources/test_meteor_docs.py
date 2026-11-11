from datetime import UTC, datetime
from pathlib import Path

import httpx
import respx

from evarisk.sources.http import HttpFetcher
from evarisk.sources.meteor_docs import FORECASTS, MeteorDocsAdapter
from evarisk.store.raw import RawStore

FLUX = Path("tests/fixtures/meo/flux_data_2024_excerpt.txt")


@respx.mock
def test_forecasts_saved_with_ntrs_distribution_date(tmp_path: Path) -> None:
    for forecast in FORECASTS.values():
        respx.get(forecast.pdf_url).mock(
            return_value=httpx.Response(200, content=b"%PDF-1.4 x", headers={"content-type": "application/pdf"})
        )
        respx.get(forecast.flux_url).mock(
            return_value=httpx.Response(200, content=FLUX.read_bytes(), headers={"content-type": "text/plain"})
        )
    recs = MeteorDocsAdapter().fetch_current(HttpFetcher(httpx.Client()), RawStore(tmp_path))
    by_product = {r.product: r for r in recs}
    assert set(by_product) == set(MeteorDocsAdapter.products)
    # Записи NTRS: 20230015158 (прогноз 2024, распространение 2023-11-02), 20240012381 (2025, 2024-10-18),
    # 20250009524 (2026, 2025-10-17); доступность это конец дня распространения.
    for product, available in (
        ("meo_flux_2024", datetime(2023, 11, 2, 23, 59, tzinfo=UTC)),
        ("meo_flux_2025", datetime(2024, 10, 18, 23, 59, tzinfo=UTC)),
        ("meo_flux_2026", datetime(2025, 10, 17, 23, 59, tzinfo=UTC)),
        ("LEO_Forecast_2024", datetime(2023, 11, 2, 23, 59, tzinfo=UTC)),
    ):
        rec = by_product[product]
        assert rec.published_at == available and rec.published_at_kind == "derived", product
    assert by_product["meo_flux_2026"].url.endswith("/20250009524/downloads/flux_data.txt")
    assert "NTRS 20240012381" in by_product["meo_flux_2025"].data_version
