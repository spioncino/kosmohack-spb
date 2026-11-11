import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from evarisk.orbit.oem import creation_date, oem_availability
from evarisk.parsers.donki import parse_notifications
from evarisk.parsers.swpc_text import issued_at
from evarisk.sources.http import http_date
from evarisk.sources.meteor_docs import FORECASTS
from evarisk.sources.meteor_docs import NOTES as MEO_NOTES
from evarisk.store.raw import RawStore
from evarisk.timeutil import parse_utc
from evarisk.types import PublishedAtKind

GFZ_DEFINITIVE_AVAILABLE = datetime(2024, 7, 15, tzinfo=UTC)
NCEI_PRODUCTS = {
    "3day_forecast": "3day_forecast",
    "discussion": "forecast_discussion",
    "geomag_forecast": "geomag_forecast",
    "daypre": "daypre",
    "space_weather_indices": "dayind",
    "solar_geophysical_activity_summaries": "sgas",
    "reports_solar_geophysical_activity": "rsga",
    "geoalerts": "geoalert",
}
_ISSUE_VERSIONED = {"3day_forecast", "forecast_discussion"}
_GOES = re.compile(r"sci_sgps-l2-avg5m_g(\d\d)_d(\d{8})_(v[\d-]+)\.nc$")
_ISSUE_HHMM = re.compile(r"^\d{8}(\d{4})")
UNRECOGNIZED_SHOWN = 3


@dataclass(frozen=True)
class ImportSummary:
    imported: int
    skipped: int
    unknown_version: int
    amended: int = 0
    unrecognized: int = 0
    unrecognized_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class Classified:
    source: str
    product: str
    published_at: datetime | None
    kind: PublishedAtKind
    data_version: str
    notes: str = ""


def _classify_ncei(parts: list[str], content: bytes) -> Classified:
    product = NCEI_PRODUCTS[parts[1]]
    name = parts[-1]
    text = content.decode(errors="replace")
    try:
        issued = issued_at(text)
    except ValueError:
        return Classified("swpc_archive", product, None, "unknown", "no-issued", "нет строки :Issued:")
    m = _ISSUE_HHMM.match(name)
    version = f"issue {m.group(1)}" if m and product in _ISSUE_VERSIONED else "daily"
    return Classified("swpc_archive", product, issued, "exact", version)


def _goes_day(m: re.Match[str]) -> date:
    ymd = m.group(2)
    return date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))


def goes_file_day(name: str) -> date | None:
    """Сутки наблюдений суточного файла GOES SGPS по имени или url (`..._d20240510_v3-0-2.nc`); иначе None."""
    m = _GOES.search(name)
    return None if m is None else _goes_day(m)


def goes_availability(day: date, last_modified: datetime | None) -> tuple[datetime, str]:
    """Момент, с которого суточный файл GOES SGPS за сутки `day` можно было скачать, и заметка о правиле.

    Правило D19 «файл за сутки D доступен с D+1 00:05Z» это нижняя граница: NCEI выкладывает переобработанный файл
    позже, файл за 10 мая 2024 появился 17 мая 2024 05:06:54 (HTTP Last-Modified). Для строгого replay доступность
    это позднее из двух времён; без Last-Modified время появления файла неизвестно и остаётся нижняя граница.
    """
    next_day = datetime.combine(day + timedelta(days=1), time(0, 5), tzinfo=UTC)
    if last_modified is None:
        return next_day, (
            f"доступность принята по правилу «следующие сутки 00:05Z» ({next_day.isoformat()}): "
            "время появления файла (Last-Modified) неизвестно"
        )
    if last_modified > next_day:
        return last_modified, (
            f"доступность по HTTP Last-Modified файла {last_modified.isoformat()}; "
            f"правило «следующие сутки 00:05Z» дало бы {next_day.isoformat()}"
        )
    return next_day, (
        f"доступность по правилу «следующие сутки 00:05Z» ({next_day.isoformat()}); "
        f"HTTP Last-Modified файла {last_modified.isoformat()} не позже"
    )


def _classify_goes(name: str, last_modified: datetime | None) -> Classified:
    m = _GOES.search(name)
    if m is None:
        raise ValueError(f"неожиданное имя файла GOES: {name}")
    published, note = goes_availability(_goes_day(m), last_modified)
    return Classified(
        "goes_sgps",
        "sgps_l2_avg5m",
        published,
        "derived",
        f"reprocessed {m.group(3)}",
        f"D22: переобработанный научный ряд NCEI; {note}",
    )


def _classify_donki(content: bytes) -> Classified:
    notes = parse_notifications(content)
    if not notes:
        return Classified("donki", "notifications", None, "unknown", "notifications", "пустой ответ")
    return Classified(
        "donki",
        "notifications",
        max(n.issued_at for n in notes),
        "derived",
        "notifications",
        "агрегат; messageIssueTime каждого сообщения внутри",
    )


_MEO_PDF = re.compile(r"^LEO_Forecast_(\d{4})\.pdf$")
_MEO_FLUX = re.compile(r"^meo_(\d{4})_flux_data\.txt$")


def _classify_meteors(name: str) -> Classified:
    """Прогноз NASA MEO: PDF и почасовые данные года; доступность по дате распространения записи NTRS."""
    for pattern, product_of in ((_MEO_PDF, "pdf"), (_MEO_FLUX, "flux")):
        m = pattern.match(name)
        if m is None:
            continue
        forecast = FORECASTS.get(int(m.group(1)))
        if forecast is None:
            raise ValueError(f"прогноз NASA MEO на {m.group(1)} год не зарегистрирован: {name}")
        product = forecast.pdf_product if product_of == "pdf" else forecast.flux_product
        return Classified("meteor_docs", product, forecast.available, "derived", forecast.version, MEO_NOTES)
    return Classified("meteor_docs", Path(name).stem, None, "unknown", "документ", "документ без даты выпуска")


def classify(rel: str, content: bytes, last_modified: datetime | None = None) -> Classified:
    parts = rel.split("/")
    name = parts[-1]
    if parts[0] == "ncei":
        return _classify_ncei(parts, content)
    if parts[0] == "nasa_oem":
        created = creation_date(content)
        available, note = oem_availability(created, last_modified)
        return Classified("nasa_oem", "oem", available, "exact", f"oem {created.isoformat()}", note)
    if parts[0] == "goes18_sgps_avg5m":
        return _classify_goes(name, last_modified)
    if parts[0] == "donki":
        return _classify_donki(content)
    if parts[0] == "gfz":
        return Classified(
            "gfz_kp",
            "kp",
            GFZ_DEFINITIVE_AVAILABLE,
            "derived",
            "definitive",
            "D21: definitive Kp доступен задним числом; в replay не участвует",
        )
    if parts[0] == "meteors":
        return _classify_meteors(name)
    if parts[0] == "swpc_spe":
        return Classified("swpc_spe", "sep_list", None, "unknown", "page", "страница списка событий")
    raise ValueError(f"неизвестный раздел зеркала: {parts[0]}")


def entry_name(line: str) -> str:
    """Имя файла из строки манифеста; у нечитаемой строки её начало."""
    try:
        return str(json.loads(line)["path"])
    except (ValueError, KeyError, TypeError):
        return line[:60]


def import_mirror(mirror_root: Path, store: RawStore) -> ImportSummary:
    """Импорт зеркала по манифесту; строка, которую не удалось прочитать или классифицировать, пропускается.

    Зеркало собирают внешние службы: файл бывает заглушкой вместо продукта, обрезанным или из неизвестного
    раздела. Такая строка не останавливает импорт остальных, а считается в `unrecognized`.
    """
    imported = skipped = unknown = amended = 0
    unrecognized: list[str] = []
    manifest = mirror_root / "manifest.jsonl"
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry: dict[str, Any] = json.loads(line)
            rel = str(entry["path"])
            content = (mirror_root / rel).read_bytes()
            c = classify(rel, content, http_date(entry.get("last_modified")))
            rec, created = store.put(
                source=c.source,
                product=c.product,
                url=str(entry["url"]),
                content=content,
                fetched_at=parse_utc(str(entry["fetched_at"])),
                published_at=c.published_at,
                published_at_kind=c.kind,
                content_type=str(entry.get("content_type") or "application/octet-stream"),
                http_status=int(entry.get("status") or 200),
                data_version=c.data_version,
                notes=c.notes,
            )
        except (ValueError, KeyError, OSError) as exc:
            unrecognized.append(f"{entry_name(line)}: {type(exc).__name__}: {exc}")
            continue
        if created:
            imported += 1
            if c.kind == "unknown" and c.source == "swpc_archive":
                unknown += 1
        elif rec.published_at != c.published_at:
            store.amend_availability(rec.id, c.published_at, c.kind, c.notes or "доступность пересчитана при импорте")
            amended += 1
        else:
            skipped += 1
    return ImportSummary(
        imported=imported,
        skipped=skipped,
        unknown_version=unknown,
        amended=amended,
        unrecognized=len(unrecognized),
        unrecognized_names=tuple(unrecognized[:UNRECOGNIZED_SHOWN]),
    )
