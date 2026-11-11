"""Докачка архива по запросу (D59): какие сутки периода не покрыты хранилищем и их загрузка через зеркало.

Покрытие меряется суточными файлами потока протонов `goes_sgps`: сутки данных берутся из имени файла в url
(`_d20240510_`): файл NCEI появляется на несколько суток позже суток наблюдения (HTTP Last-Modified); запись
без суток в имени относится к дате доступности минус один день. Докачка идёт тем же кодом, что ручное зеркало, и тем
же импортом: классификация записей и правило времени доступности (D57, GOES по Last-Modified) совпадают, отсечение
`AsOf` работает одинаково.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from evarisk.sources.archive_mirror import Fetch, Mirror, Report, http_fetch, mirror_meteor_forecasts, mirror_period
from evarisk.sources.meteor_docs import FORECASTS, flux_product
from evarisk.store.mirror_import import goes_file_day, import_mirror
from evarisk.store.raw import RawStore
from evarisk.types import Window

GOES_SOURCE = "goes_sgps"
# Прогнозы SWPC, Kp и сообщения DONKI нужны за несколько суток до отсечения: идущее событие начинается раньше окна.
LEAD_DAYS = 3
# В replay бейзлайн персистентности ищет последний доступный к отсечению отсчёт GOES за 10 суток до него
# (`replay.baseline.PERSISTENCE_LOOKBACK`): суточный файл NCEI доступен через несколько суток после суток наблюдения,
# и без этих суток в свежем хранилище бейзлайн не находил отсчёта, который видит хранилище с полным зеркалом.
REPLAY_LEAD_DAYS = 10
# Суточный файл выходит на следующий день; вторые сутки это запас на задержку публикации архива.
PUBLICATION_LAG_DAYS = 2
FAILED_SHOWN = 3
NOTE_FETCHED = "архив на период докачан по запросу: {start} … {end}, новых файлов {new}, записей в хранилище {imported}"
NOTE_STILL_MISSING = "в архиве GOES-18 SGPS нет суточных файлов потока протонов за {ranges}"
NOTE_FAILED = "ошибок загрузки {count}, первые: {shown}"
NOTE_UNRECOGNIZED = "файлов зеркала не распознано: {count}, первые: {shown}"


def covered_days(store: RawStore) -> set[date]:
    """Сутки, за которые в хранилище есть суточный файл потока протонов: по имени файла в url, иначе по доступности."""
    days: set[date] = set()
    for r in store.records(GOES_SOURCE):
        day = goes_file_day(r.url)
        if day is None and r.published_at is not None:
            day = (r.published_at - timedelta(days=1)).date()
        if day is not None:
            days.add(day)
    return days


def day_ranges(days: list[date]) -> list[tuple[date, date]]:
    """Отсортированные сутки, склеенные в непрерывные диапазоны: соседние даты идут в один диапазон."""
    ranges: list[tuple[date, date]] = []
    for day in days:
        if ranges and day - ranges[-1][1] <= timedelta(days=1):
            ranges[-1] = (ranges[-1][0], day)
        else:
            ranges.append((day, day))
    return ranges


def ranges_text(days: list[date]) -> str:
    return ", ".join(f"{first.isoformat()} … {last.isoformat()}" for first, last in day_ranges(sorted(days)))


def needed_days(span: Window, cutoff: datetime | None, today: date) -> list[date]:
    """Сутки, данные которых нужны расчёту: от `LEAD_DAYS` до начала поиска, а при отсечении от `REPLAY_LEAD_DAYS`
    до него, до конца поиска.

    Сутки позже `today − PUBLICATION_LAG_DAYS` не входят: их суточного файла в архиве ещё нет.
    """
    first = span.start.date() - timedelta(days=LEAD_DAYS)
    if cutoff is not None:
        first = min(first, cutoff.date() - timedelta(days=REPLAY_LEAD_DAYS))
    last = min((span.end - timedelta(microseconds=1)).date(), today - timedelta(days=PUBLICATION_LAG_DAYS))
    count = (last - first).days + 1
    return [first + timedelta(days=i) for i in range(max(0, count))]


def missing_forecast_years(store: RawStore, span: Window) -> set[int]:
    """Годы периода, для которых в NTRS есть почасовой прогноз NASA MEO, а в хранилище его записи нет (D67)."""
    years = set(range(span.start.year, span.end.year + 1))
    return {y for y in years if y in FORECASTS and not store.records(source="meteor_docs", product=flux_product(y))}


def missing_days(store: RawStore, span: Window, cutoff: datetime | None, today: date) -> list[date]:
    have = covered_days(store)
    return [d for d in needed_days(span, cutoff, today) if d not in have]


@dataclass(frozen=True)
class BackfillReport:
    start: date
    end: date
    new_files: int
    imported: int
    failed: tuple[str, ...]
    still_missing: tuple[date, ...]
    unrecognized: int = 0
    unrecognized_names: tuple[str, ...] = ()

    def notes(self) -> list[str]:
        """Строки для ограничений результата: что докачано, каких суток в архиве нет, сколько загрузок не удалось
        и сколько файлов зеркала не распознано."""
        out = [
            NOTE_FETCHED.format(
                start=self.start.isoformat(), end=self.end.isoformat(), new=self.new_files, imported=self.imported
            )
        ]
        if self.still_missing:
            out.append(NOTE_STILL_MISSING.format(ranges=ranges_text(list(self.still_missing))))
        if self.failed:
            shown = "; ".join(self.failed[:FAILED_SHOWN])
            out.append(NOTE_FAILED.format(count=len(self.failed), shown=shown))
        if self.unrecognized:
            out.append(NOTE_UNRECOGNIZED.format(count=self.unrecognized, shown="; ".join(self.unrecognized_names)))
        return out


def backfill(
    store: RawStore,
    mirror_root: Path,
    span: Window,
    cutoff: datetime | None,
    now: datetime,
    *,
    fetch: Fetch = http_fetch,
    report: Report = print,
) -> BackfillReport | None:
    """Докачка непокрытых суток периода и годовых прогнозов NASA MEO; `None`, когда всё уже в хранилище.

    Отказ сети исключений не даёт: неудачные загрузки перечислены в отчёте, расчёт идёт по тому, что есть.
    """
    missing = missing_days(store, span, cutoff, now.date())
    years = missing_forecast_years(store, span)
    if not missing and not years:
        return None
    mirror = Mirror(mirror_root, fetch=fetch, report=report)
    if missing:
        start, end = missing[0], missing[-1]
        # Документы (прогнозы NASA MEO на годы этих суток и список событий NOAA) берутся вместе с архивом.
        mirror_period(mirror, start, end, docs=True, current_oem=False)
    else:
        start, end = span.start.date(), span.end.date()
    # Прогноз MEO на год окна нужен и тогда, когда поток протонов за эти сутки уже в хранилище.
    mirror_meteor_forecasts(mirror, years)
    summary = import_mirror(mirror_root, store) if mirror.manifest.exists() else None
    return BackfillReport(
        start=start,
        end=end,
        new_files=mirror.new,
        imported=summary.imported if summary else 0,
        failed=tuple(mirror.failed),
        still_missing=tuple(missing_days(store, span, cutoff, now.date())),
        unrecognized=summary.unrecognized if summary else 0,
        unrecognized_names=summary.unrecognized_names if summary else (),
    )
