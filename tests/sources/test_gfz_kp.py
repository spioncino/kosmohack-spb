import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from evarisk.sources import gfz_kp
from evarisk.sources.gfz_kp import URL, GfzKpAdapter
from evarisk.sources.http import HttpFetcher
from evarisk.store.raw import RawStore

FIX = Path("tests/fixtures/gfz/kp_2024-05-10_11.json").read_bytes()
JSON = {"content-type": "application/json"}


def _nowcast_payload() -> bytes:
    data = json.loads(FIX)
    data["status"][-1] = "nowcast"
    return json.dumps(data).encode()


@respx.mock
def test_archive_marks_derived_publication_and_definitive_version(tmp_path: Path) -> None:
    route = respx.get(url__startswith=URL).mock(return_value=httpx.Response(200, content=FIX, headers=JSON))
    recs = GfzKpAdapter().fetch_archive(
        HttpFetcher(httpx.Client()), RawStore(tmp_path), date(2024, 5, 10), date(2024, 5, 11)
    )
    q = str(route.calls[0].request.url)
    assert "start=2024-05-10T00%3A00%3A00Z" in q and "end=2024-05-11T23%3A59%3A59Z" in q and "index=Kp" in q
    assert recs[0].published_at_kind == "derived"
    assert recs[0].published_at == datetime(2024, 5, 12, 1, 0, tzinfo=UTC)
    assert recs[0].notes.startswith("D18")
    assert recs[0].data_version == "definitive"


@respx.mock
def test_any_nowcast_value_makes_version_nowcast(tmp_path: Path) -> None:
    respx.get(url__startswith=URL).mock(return_value=httpx.Response(200, content=_nowcast_payload(), headers=JSON))
    recs = GfzKpAdapter().fetch_archive(
        HttpFetcher(httpx.Client()), RawStore(tmp_path), date(2024, 5, 10), date(2024, 5, 11)
    )
    assert recs[0].data_version == "nowcast"
    assert recs[0].published_at == datetime(2024, 5, 12, 1, 0, tzinfo=UTC)


@respx.mock
def test_current_covers_last_three_days(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gfz_kp, "utc_now", lambda: datetime(2024, 5, 12, 6, 30, tzinfo=UTC))
    route = respx.get(url__startswith=URL).mock(return_value=httpx.Response(200, content=FIX, headers=JSON))
    recs = GfzKpAdapter().fetch_current(HttpFetcher(httpx.Client()), RawStore(tmp_path))
    q = str(route.calls[0].request.url)
    assert "start=2024-05-09T06%3A30%3A00Z" in q and "end=2024-05-12T06%3A30%3A00Z" in q
    assert len(recs) == 1 and recs[0].product == "kp"


@respx.mock
def test_empty_answer_is_unknown(tmp_path: Path) -> None:
    empty = json.dumps({"Kp": [], "datetime": [], "status": []}).encode()
    respx.get(url__startswith=URL).mock(return_value=httpx.Response(200, content=empty, headers=JSON))
    recs = GfzKpAdapter().fetch_archive(
        HttpFetcher(httpx.Client()), RawStore(tmp_path), date(2024, 5, 10), date(2024, 5, 11)
    )
    assert recs[0].published_at_kind == "unknown" and recs[0].published_at is None
    assert recs[0].data_version == "empty"
