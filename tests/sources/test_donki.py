from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from evarisk.sources import donki
from evarisk.sources.donki import BASE, DonkiAdapter
from evarisk.sources.http import HttpFetcher
from evarisk.store.raw import RawStore

FIX = Path("tests/fixtures/donki/notifications_2024-05-09_2024-05-12.json").read_bytes()
JSON = {"content-type": "application/json"}


@respx.mock
def test_archive_chunks_by_week_and_marks_publication(tmp_path: Path) -> None:
    route = respx.get(url__startswith=BASE).mock(
        side_effect=[httpx.Response(200, content=FIX, headers=JSON), httpx.Response(200, content=b"[]", headers=JSON)]
    )
    recs = DonkiAdapter().fetch_archive(
        HttpFetcher(httpx.Client()), RawStore(tmp_path), date(2024, 5, 9), date(2024, 5, 20)
    )
    assert route.call_count == 2
    first, second = str(route.calls[0].request.url), str(route.calls[1].request.url)
    assert "startDate=2024-05-09" in first and "endDate=2024-05-15" in first
    assert "startDate=2024-05-16" in second and "endDate=2024-05-20" in second
    assert recs[0].published_at_kind == "derived" and recs[0].published_at is not None
    assert recs[0].published_at >= datetime(2024, 5, 11, 12, 33, tzinfo=UTC)
    assert recs[0].data_version == "notifications"
    assert recs[1].published_at_kind == "unknown" and recs[1].notes == "пустой ответ"
    assert recs[1].data_version == "notifications"


@respx.mock
def test_current_takes_last_seven_days(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(donki, "utc_now", lambda: datetime(2024, 5, 12, 6, 0, tzinfo=UTC))
    route = respx.get(url__startswith=BASE).mock(return_value=httpx.Response(200, content=FIX, headers=JSON))
    recs = DonkiAdapter().fetch_current(HttpFetcher(httpx.Client()), RawStore(tmp_path))
    url = str(route.calls[0].request.url)
    assert "startDate=2024-05-05" in url and "endDate=2024-05-12" in url and "type=all" in url
    assert len(recs) == 1 and recs[0].product == "notifications" and recs[0].published_at_kind == "derived"
