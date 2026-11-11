import http.client
import json
import urllib.request
from datetime import date
from pathlib import Path

import pytest

from evarisk.sources import archive_mirror
from evarisk.sources.archive_mirror import DONKI, GOES, Mirror, mirror_donki, mirror_goes, months


def quiet(_: str) -> None:
    return None


class Recorder:
    def __init__(self, pages: dict[str, bytes]) -> None:
        self.pages = pages
        self.urls: list[str] = []

    def __call__(self, url: str) -> tuple[int, bytes, dict[str, str]]:
        self.urls.append(url)
        if url in self.pages:
            return (
                200,
                self.pages[url],
                {"last-modified": "Mon, 13 May 2024 03:06:00 GMT", "content-type": "text/plain"},
            )
        return 404, b"", {}


def test_get_writes_the_file_and_a_manifest_row_and_skips_it_next_time(tmp_path: Path) -> None:
    fetch = Recorder({"https://x.invalid/a.txt": b"abc"})
    mirror = Mirror(tmp_path, fetch=fetch, report=quiet)
    assert mirror.get("dir/a.txt", "https://x.invalid/a.txt") == b"abc"
    row = json.loads((tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert (row["path"], row["size"], row["status"]) == ("dir/a.txt", 3, 200)
    # sha256 строки «abc» по FIPS 180-2, приложение B.1.
    assert row["sha256"] == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert row["last_modified"] == "Mon, 13 May 2024 03:06:00 GMT"
    again = Mirror(tmp_path, fetch=fetch, report=quiet)
    assert again.get("dir/a.txt", "https://x.invalid/a.txt") == b"abc"
    assert (again.new, again.skipped, len(fetch.urls)) == (0, 1, 1)


def test_missing_file_is_a_failure_without_a_manifest_row(tmp_path: Path) -> None:
    mirror = Mirror(tmp_path, fetch=Recorder({}), report=quiet)
    assert mirror.get("dir/b.txt", "https://x.invalid/b.txt") is None
    assert mirror.failed == ["https://x.invalid/b.txt: HTTP 404"]
    assert not (tmp_path / "manifest.jsonl").exists()


def test_goes_takes_only_days_inside_the_period(tmp_path: Path) -> None:
    names = [f"sci_sgps-l2-avg5m_g18_d202511{day:02d}_v3-0-2.nc" for day in (8, 9, 10, 11)]
    listing = "".join(f'<a href="{n}">{n}</a>' for n in names).encode()
    pages = {f"{GOES}/2025/11/": listing} | {f"{GOES}/2025/11/{n}": n.encode() for n in names}
    mirror = Mirror(tmp_path, fetch=Recorder(pages), report=quiet)
    mirror_goes(mirror, date(2025, 11, 9), date(2025, 11, 10))
    saved = sorted(p.name for p in (tmp_path / "goes18_sgps_avg5m/2025/11").iterdir())
    assert saved == names[1:3]


def test_donki_is_requested_in_seven_day_chunks(tmp_path: Path) -> None:
    fetch = Recorder({})
    mirror_donki(Mirror(tmp_path, fetch=fetch, report=quiet), date(2025, 11, 1), date(2025, 11, 13))
    assert fetch.urls == [
        f"{DONKI}?startDate=2025-11-01&endDate=2025-11-07&type=all",
        f"{DONKI}?startDate=2025-11-08&endDate=2025-11-13&type=all",
    ]


def test_months_cross_the_year_boundary() -> None:
    assert months(date(2025, 12, 30), date(2026, 1, 2)) == [(2025, 12), (2026, 1)]


class Torn:
    """Подменённая загрузка: тело ответа обрывается, как у NCEI и S3 при разрыве соединения."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str) -> tuple[int, bytes, dict[str, str]]:
        self.urls.append(url)
        raise http.client.IncompleteRead(b"abc", 97)


def test_torn_response_is_a_failure_and_does_not_raise(tmp_path: Path) -> None:
    fetch = Torn()
    mirror = Mirror(tmp_path, fetch=fetch, report=quiet)
    assert mirror.get("dir/a.txt", "https://x.invalid/a.txt") is None
    assert mirror.new == 0 and not (tmp_path / "manifest.jsonl").exists()
    assert mirror.failed == ["https://x.invalid/a.txt: IncompleteRead: IncompleteRead(3 bytes read, 97 more expected)"]
    assert mirror.listing("https://x.invalid/dir/") == []
    assert len(mirror.failed) == 2 and "IncompleteRead" in mirror.failed[1]
    assert fetch.urls == ["https://x.invalid/a.txt", "https://x.invalid/dir/"]


def test_write_failure_is_a_failure_and_does_not_raise(tmp_path: Path) -> None:
    mirror = Mirror(tmp_path, fetch=Recorder({"https://x.invalid/a.txt": b"abc"}), report=quiet)
    (tmp_path / "dir").write_text("не каталог", encoding="utf-8")
    assert mirror.get("dir/a.txt", "https://x.invalid/a.txt") is None
    assert mirror.new == 0 and mirror.failed and "запись dir/a.txt" in mirror.failed[0]


def test_http_fetch_retries_a_torn_body_and_then_reports_it() -> None:
    calls: list[str] = []

    def urlopen(_request: object, timeout: int = 0) -> object:
        calls.append("try")
        raise http.client.IncompleteRead(b"", 10)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(urllib.request, "urlopen", urlopen)
        mp.setattr(archive_mirror.time, "sleep", lambda _s: None)
        with pytest.raises(RuntimeError) as err:
            archive_mirror.http_fetch("https://x.invalid/a.txt", retries=3)
    assert len(calls) == 3 and "IncompleteRead" in str(err.value)
