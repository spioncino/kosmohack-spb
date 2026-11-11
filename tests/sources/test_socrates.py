from datetime import UTC, datetime
from pathlib import Path

import httpx
import respx

from evarisk.sources.http import HttpFetcher
from evarisk.sources.socrates import URL, SocratesAdapter
from evarisk.store.raw import RawStore

HTML = Path("tests/fixtures/socrates/table_iss_2026-09-17.html").read_bytes()


@respx.mock
def test_snapshot_uses_data_as_of(tmp_path: Path) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, content=HTML, headers={"content-type": "text/html"}))
    recs = SocratesAdapter().fetch_current(HttpFetcher(httpx.Client()), RawStore(tmp_path))
    assert len(recs) == 1
    assert recs[0].published_at == datetime(2026, 9, 17, 22, 14, 23, tzinfo=UTC)
    assert recs[0].published_at_kind == "exact" and recs[0].path.endswith(".html")
    assert recs[0].data_version == "run 2026-09-17T22:14:23+00:00"
    assert recs[0].product == "conjunctions_iss" and SocratesAdapter().supports_archive is False
