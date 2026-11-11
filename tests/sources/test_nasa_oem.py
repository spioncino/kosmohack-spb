from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import respx

from evarisk.sources.http import HttpFetcher
from evarisk.sources.nasa_oem import S3, NasaOemAdapter
from evarisk.store.raw import RawStore

OEM = Path("tests/fixtures/nasa_oem/2024-05-12_ISS.OEM_J2K_EPH.txt").read_bytes()
LIST = Path("tests/fixtures/nasa_oem/s3_list_2024-05.xml").read_bytes()
CREATED = datetime(2024, 5, 10, 18, 51, 53, 202000, tzinfo=UTC)
VERSION = "oem 2024-05-10T18:51:53.202000+00:00"


@respx.mock
def test_list_days_parses_s3_prefixes() -> None:
    respx.get(f"{S3}/", params__contains={"prefix": "iss-coords/2024-05-"}).mock(
        return_value=httpx.Response(200, content=LIST, headers={"content-type": "application/xml"})
    )
    days = NasaOemAdapter().list_days(HttpFetcher(httpx.Client()), "2024-05-")
    assert len(days) == 13
    assert date(2024, 5, 12) in days and all(d.month == 5 for d in days)


@respx.mock
def test_fetch_archive_saves_with_creation_date(tmp_path: Path) -> None:
    respx.get(f"{S3}/", params__contains={"prefix": "iss-coords/2024-05-"}).mock(
        return_value=httpx.Response(200, content=LIST, headers={"content-type": "application/xml"})
    )
    respx.get(url__regex=rf"{S3}/iss-coords/2024-05-\d\d/ISS_OEM/ISS.OEM_J2K_EPH.txt").mock(
        return_value=httpx.Response(200, content=OEM, headers={"content-type": "text/plain"})
    )
    store = RawStore(tmp_path)
    recs = NasaOemAdapter().fetch_archive(HttpFetcher(httpx.Client()), store, date(2024, 5, 12), date(2024, 5, 13))
    assert len(recs) == 2
    assert recs[0].published_at == CREATED
    assert recs[0].published_at_kind == "exact" and recs[0].product == "oem"
    assert recs[0].data_version == VERSION


@respx.mock
def test_fetch_current_uses_current_folder(tmp_path: Path) -> None:
    respx.get(f"{S3}/iss-coords/current/ISS_OEM/ISS.OEM_J2K_EPH.txt").mock(
        return_value=httpx.Response(200, content=OEM, headers={"content-type": "text/plain"})
    )
    recs = NasaOemAdapter().fetch_current(HttpFetcher(httpx.Client()), RawStore(tmp_path))
    assert len(recs) == 1 and recs[0].published_at_kind == "exact"
    assert recs[0].published_at == CREATED and recs[0].data_version == VERSION
