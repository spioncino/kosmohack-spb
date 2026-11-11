import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from evarisk.sources.http import HttpFetcher
from evarisk.sources.swpc_live import PRODUCTS, SwpcLiveAdapter, latest_alert_issue
from evarisk.store.raw import RawStore

FIX = Path("tests/fixtures/swpc_live")
FILES = {
    "3day_forecast": "3-day-forecast.txt",
    "kp_1m": "planetary_k_index_1m.json",
    "kp_forecast": "noaa-planetary-k-index-forecast.json",
    "scales": "noaa-scales.json",
    "alerts": "alerts.json",
    "protons_7d": "integral-protons-7-day.json",
    "instrument_sources": "instrument-sources.json",
}


def test_latest_alert_issue() -> None:
    content = json.dumps(
        [
            {"issue_datetime": "2026-09-16 05:38:03.320", "message": "x"},
            {"issue_datetime": "2026-09-17 01:02:03.000", "message": "y"},
        ]
    ).encode()
    assert latest_alert_issue(content) == datetime(2026, 9, 17, 1, 2, 3, tzinfo=UTC)


def test_latest_alert_issue_rejects_empty() -> None:
    with pytest.raises(ValueError):
        latest_alert_issue(b"[]")


@respx.mock
def test_fetch_current_saves_every_product(tmp_path: Path) -> None:
    for product, url in PRODUCTS.items():
        ctype = "text/plain" if url.endswith(".txt") else "application/json"
        respx.get(url).mock(
            return_value=httpx.Response(
                200, content=(FIX / FILES[product]).read_bytes(), headers={"content-type": ctype}
            )
        )
    recs = SwpcLiveAdapter().fetch_current(HttpFetcher(httpx.Client()), RawStore(tmp_path))
    by = {r.product: r for r in recs}
    assert set(by) == set(PRODUCTS)
    assert by["3day_forecast"].published_at_kind == "exact"
    assert by["3day_forecast"].published_at == datetime(2026, 9, 18, 12, 30, tzinfo=UTC)
    assert by["3day_forecast"].data_version == "issue 1230"
    assert by["alerts"].published_at_kind == "derived" and by["alerts"].published_at is not None
    assert by["alerts"].published_at >= datetime(2026, 9, 16, 5, 38, 3, tzinfo=UTC)
    assert by["kp_1m"].published_at == by["kp_1m"].fetched_at
    assert all(by[p].data_version == "live" for p in PRODUCTS if p != "3day_forecast")
    protons = json.loads((tmp_path / by["protons_7d"].path).read_bytes())
    assert {"time_tag", "flux", "energy", "satellite"} <= set(protons[0])
