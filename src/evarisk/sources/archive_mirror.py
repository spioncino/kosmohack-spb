"""Зеркало открытых архивов за заданный период в каталоге с манифестом на каждый файл.

Манифест `manifest.jsonl`: url, path, fetched_at, sha256, size, status, last_modified, etag, content_type.
Файл, который уже есть на диске и в манифесте, не перекачивается. Только стандартная библиотека.
Модулем пользуются ручной скрипт `scripts/mirror_archives.py` и докачка архива по запросу (D59).
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from evarisk.sources.meteor_docs import FLUX_FILE, FORECASTS

UA = "evarisk-mirror/0.1 (kosmohackathon-2026)"
NCEI = "https://www.ngdc.noaa.gov/stp/space-weather/swpc-products/daily_reports"
NCEI_PRODUCTS = (
    "3day_forecast",
    "discussion",
    "geomag_forecast",
    "daypre",
    "space_weather_indices",
    "solar_geophysical_activity_summaries",
    "reports_solar_geophysical_activity",
    "geoalerts",
)
S3 = "https://nasa-public-data.s3.amazonaws.com"
GOES = "https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/goes/goes18/l2/data/sgps-l2-avg5m"
DONKI = "https://kauai.ccmc.gsfc.nasa.gov/DONKI/WS/get/notifications"
GFZ = "https://kp.gfz.de/app/json/"
DOCS = {
    "swpc_spe/sep_page_code.html": (
        "https://www.ngdc.noaa.gov/stp/space-weather/interplanetary-data/solar-proton-events/SEP%20page%20code.html"
    ),
}
# Эфемерида выпускается раз в два-три дня на 15 суток вперёд: для прогноза из прошлого на первые сутки периода
# нужен файл, выпущенный до них, поэтому эфемериды берутся с запасом до начала периода.
OEM_LEAD_DAYS = 10
DONKI_CHUNK_DAYS = 7
NCEI_PAUSE_S = 0.15
_HREF = re.compile(r'href="([^"?/][^"]*)"')
_OEM_PREFIX = re.compile(r"<Prefix>iss-coords/(\d{4}-\d{2}-\d{2})/</Prefix>")
_GOES_NAME = re.compile(r"sci_sgps-l2-avg5m_g18_d(\d{8})_v[\d-]+\.nc$")

Fetch = Callable[[str], tuple[int, bytes, dict[str, str]]]
Report = Callable[[str], None]


def http_fetch(url: str, retries: int = 4) -> tuple[int, bytes, dict[str, str]]:
    """Статус, тело и заголовки; ответы 4xx возвращаются без повторов, сетевые ошибки и обрыв тела повторяются."""
    last = ""
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.status, resp.read(), {k.lower(): v for k, v in resp.headers.items()}
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500:
                return exc.code, b"", {}
            last = f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            # http.client.IncompleteRead приходит от NCEI и S3 на оборванном теле ответа: это повод повторить.
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(2 * attempt)
    raise RuntimeError(f"{url}: {last}")


class Mirror:
    def __init__(self, root: Path, fetch: Fetch = http_fetch, report: Report = print) -> None:
        self.root = root
        self.fetch = fetch
        self.report = report
        self.manifest = root / "manifest.jsonl"
        self.root.mkdir(parents=True, exist_ok=True)
        self.have: set[str] = set()
        if self.manifest.exists():
            for line in self.manifest.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.have.add(json.loads(line)["path"])
        self.new = 0
        self.skipped = 0
        self.failed: list[str] = []

    def _fail(self, text: str, *, report: bool = True) -> None:
        self.failed.append(text)
        if report:
            self.report(f"  ОШИБКА {text}")

    def _write(self, rel: str, url: str, status: int, content: bytes, headers: dict[str, str]) -> None:
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        entry = {
            "url": url,
            "path": rel,
            "fetched_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
            "status": status,
            "last_modified": headers.get("last-modified"),
            "etag": headers.get("etag"),
            "content_type": headers.get("content-type"),
        }
        with self.manifest.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get(self, rel: str, url: str) -> bytes | None:
        """Файл зеркала; любой отказ загрузки или записи это `None` и строка в `failed`, а не исключение.

        Докачка идёт внутри расчёта по запросу: обрыв тела ответа, ошибка службы или переполненный диск на одном
        файле не должны прекращать зеркалирование остальных.
        """
        target = self.root / rel
        if rel in self.have and target.exists():
            self.skipped += 1
            return target.read_bytes()
        try:
            status, content, headers = self.fetch(url)
        except RuntimeError as exc:
            self._fail(str(exc))
            return None
        except Exception as exc:
            self._fail(f"{url}: {type(exc).__name__}: {exc}")
            return None
        if status != 200:
            self._fail(f"{url}: HTTP {status}")
            return None
        try:
            self._write(rel, url, status, content, headers)
        except OSError as exc:
            self._fail(f"{url}: запись {rel}: {type(exc).__name__}: {exc}")
            return None
        self.have.add(rel)
        self.new += 1
        return content

    def listing(self, url: str) -> list[str]:
        """Имена из HTML-листинга каталога; недоступный каталог это пустой список и запись в `failed`."""
        try:
            status, content, _ = self.fetch(url)
        except RuntimeError as exc:
            self._fail(str(exc), report=False)
            return []
        except Exception as exc:
            self._fail(f"{url}: {type(exc).__name__}: {exc}", report=False)
            return []
        if status != 200:
            return []
        return _HREF.findall(content.decode(errors="replace"))


def months(start: date, end: date) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def mirror_ncei(mir: Mirror, start: date, end: date, pause_s: float = NCEI_PAUSE_S) -> None:
    for product in NCEI_PRODUCTS:
        mir.report(f"NCEI {product}")
        for y, m in months(start, end):
            base = f"{NCEI}/{product}/{y:04d}/{m:02d}/"
            for name in mir.listing(base):
                if not name.endswith(".txt") or not re.match(r"\d{8}", name):
                    continue
                day = date(int(name[:4]), int(name[4:6]), int(name[6:8]))
                if start <= day <= end:
                    before = mir.new
                    mir.get(f"ncei/{product}/{y:04d}/{m:02d}/{name}", base + name)
                    if mir.new != before and pause_s:
                        time.sleep(pause_s)


def mirror_oem(mir: Mirror, start: date, end: date, *, current: bool = True) -> None:
    mir.report("NASA OEM")
    for y, m in months(start, end):
        q = urllib.parse.urlencode({"prefix": f"iss-coords/{y:04d}-{m:02d}-", "delimiter": "/", "max-keys": "1000"})
        try:
            _status, content, _ = mir.fetch(f"{S3}/?{q}")
        except RuntimeError as exc:
            mir.failed.append(str(exc))
            continue
        for d in _OEM_PREFIX.findall(content.decode(errors="replace")):
            if start <= date.fromisoformat(d) <= end:
                mir.get(f"nasa_oem/{d}/ISS.OEM_J2K_EPH.txt", f"{S3}/iss-coords/{d}/ISS_OEM/ISS.OEM_J2K_EPH.txt")
    if current:
        mir.get(
            f"nasa_oem/current_{datetime.now(tz=UTC):%Y%m%dT%H%M%SZ}/ISS.OEM_J2K_EPH.txt",
            f"{S3}/iss-coords/current/ISS_OEM/ISS.OEM_J2K_EPH.txt",
        )


def mirror_goes(mir: Mirror, start: date, end: date) -> None:
    mir.report("GOES-18 SGPS avg5m")
    for y, m in months(start, end):
        base = f"{GOES}/{y:04d}/{m:02d}/"
        for name in mir.listing(base):
            mm = _GOES_NAME.match(name)
            if not mm:
                continue
            ymd = mm.group(1)
            day = date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))
            if start <= day <= end:
                mir.get(f"goes18_sgps_avg5m/{y:04d}/{m:02d}/{name}", base + name)


def mirror_donki(mir: Mirror, start: date, end: date) -> None:
    mir.report("DONKI")
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=DONKI_CHUNK_DAYS - 1), end)
        q = urllib.parse.urlencode({"startDate": cursor.isoformat(), "endDate": stop.isoformat(), "type": "all"})
        mir.get(f"donki/notifications_{cursor}_{stop}.json", f"{DONKI}?{q}")
        cursor = stop + timedelta(days=1)


def mirror_gfz(mir: Mirror, start: date, end: date) -> None:
    mir.report("GFZ Kp")
    q = urllib.parse.urlencode({"start": f"{start}T00:00:00Z", "end": f"{end}T23:59:59Z", "index": "Kp"})
    mir.get(f"gfz/kp_{start}_{end}.json", f"{GFZ}?{q}")


def meteor_docs(years: set[int]) -> dict[str, str]:
    """PDF и почасовые данные прогнозов NASA MEO на годы `years`, для которых прогноз с данными есть в NTRS."""
    out: dict[str, str] = {}
    for year in sorted(years):
        forecast = FORECASTS.get(year)
        if forecast is None:
            continue
        out[f"meteors/{forecast.pdf}"] = forecast.pdf_url
        out[f"meteors/meo_{year}_{FLUX_FILE}"] = forecast.flux_url
    return out


def mirror_meteor_forecasts(mir: Mirror, years: set[int]) -> None:
    mir.report(f"прогнозы NASA MEO: {', '.join(str(y) for y in sorted(years))}")
    for rel, url in meteor_docs(years).items():
        mir.get(rel, url)


def mirror_period(mir: Mirror, start: date, end: date, *, docs: bool = True, current_oem: bool = True) -> None:
    """Все архивные продукты за `[start, end]`; эфемериды с запасом `OEM_LEAD_DAYS` до начала."""
    if docs:
        for rel, url in DOCS.items():
            mir.get(rel, url)
        mirror_meteor_forecasts(mir, set(range(start.year, end.year + 1)))
    mirror_gfz(mir, start, end)
    mirror_donki(mir, start, end)
    mirror_ncei(mir, start, end)
    mirror_oem(mir, start - timedelta(days=OEM_LEAD_DAYS), end, current=current_oem)
    mirror_goes(mir, start, end)
