import json
from datetime import date
from pathlib import Path

import httpx
import respx

from evarisk.sources.celestrak_gp import GP_URL, CelestrakGpAdapter
from evarisk.sources.http import HttpFetcher
from evarisk.store.raw import RawStore

CELESTRAK = Path("tests/fixtures/celestrak/gp_25544.json").read_bytes()


@respx.mock
def test_celestrak_current_is_derived_publication(tmp_path: Path) -> None:
    respx.get(GP_URL).mock(
        return_value=httpx.Response(200, content=CELESTRAK, headers={"content-type": "application/json"})
    )
    recs = CelestrakGpAdapter().fetch_current(HttpFetcher(httpx.Client()), RawStore(tmp_path))
    assert len(recs) == 1
    assert recs[0].published_at_kind == "derived" and recs[0].published_at == recs[0].fetched_at
    assert recs[0].data_version == "gp latest" and recs[0].notes
    body = json.loads((tmp_path / recs[0].path).read_bytes())
    assert body[0]["NORAD_CAT_ID"] == 25544 and 15.3 < body[0]["MEAN_MOTION"] < 15.7


def test_celestrak_has_no_archive(tmp_path: Path) -> None:
    a = CelestrakGpAdapter()
    assert a.supports_archive is False
    assert a.fetch_archive(HttpFetcher(httpx.Client()), RawStore(tmp_path), date(2024, 5, 1), date(2024, 5, 2)) == []
