import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
from numpy.typing import NDArray

from evarisk.orbit.oem import OemEphemeris, parse_oem
from evarisk.parsers.gfz_kp import parse_kp_json
from evarisk.parsers.goes_sgps import SgpsDay, integral_flux_pfu, read_sgps
from evarisk.parsers.swpc_json import parse_integral_protons
from evarisk.store.asof import AsOf, UnreadableRecord, availability_key
from evarisk.store.raw import RawRecord
from evarisk.types import Kind, Provenance, Window

_OEM_CACHE: dict[str, OemEphemeris] = {}
_SGPS_CACHE: dict[str, SgpsDay] = {}
_OEM_MARGIN = timedelta(hours=1)
_KP_STEP = timedelta(hours=3)
_THRESHOLDS_MEV = (10.0, 30.0, 50.0, 100.0)
_KEV_PER_MEV = 1000.0
PROTON_NOTE = "интегральный поток рассчитан из дифференциальных каналов (D19)"
LIVE_SOURCE = "swpc_live"
LIVE_PROTONS = "protons_7d"
LIVE_PROTON_VERSION = "swpc live integral"
LIVE_PROTON_NOTE = "официальные интегральные потоки SWPC (GOES primary), спектра нет"
UNREADABLE_LOOKBACK = timedelta(days=7)
GOES_DAY_MARGIN = timedelta(days=1)
# Сутки наблюдения в имени суточного файла GOES SGPS: sci_sgps-l2-avg5m_g18_d20240510_v3-0-2.nc.
_GOES_FILE_DAY = re.compile(r"_d(\d{8})_")

# Причина отказа по записи: разбор одного и того же содержимого не повторяется в каждом расчёте.
_FAILED: dict[str, str] = {}


def provenance_of(record: RawRecord) -> Provenance:
    return Provenance(
        raw_id=record.id,
        source=record.source,
        product=record.product,
        published_at=record.published_at,
        published_at_kind=record.published_at_kind,
        data_version=record.data_version,
        url=record.url,
    )


def message_provenance(record: RawRecord, issued_at: datetime, message_id: str) -> Provenance:
    """Провенанс сообщения внутри агрегатной записи: время публикации это выпуск сообщения, а не доступность файла.

    Файл уведомлений DONKI доступен позже своих сообщений, отсечение применяется к каждому сообщению отдельно;
    провенанс результата несёт время выпуска использованного сообщения, поэтому проверка «ничего позже
    отсечения» выполняется прямо по выгрузке. `raw_id` и `url` остаются записью файла, где лежит сообщение.
    """
    return provenance_of(record).model_copy(
        update={"published_at": issued_at, "published_at_kind": "exact", "data_version": f"сообщение {message_id}"}
    )


def read_parsed[T](asof: AsOf, record: RawRecord, parse: Callable[[bytes], T]) -> T | None:
    """Разбор одной записи; при любом отказе чтения или разбора запись пропускается и причина уходит в `asof`.

    Содержимое приходит из внешних служб и бывает обрезанным, подменённым заглушкой или в неизвестной версии
    формата. Отказ одной записи слоя не останавливает расчёт: остальные записи читаются, а пропущенные
    перечисляет `unreadable_records`.
    """
    reason = _FAILED.get(record.id)
    if reason is None:
        try:
            return parse(asof.read(record))
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            _FAILED[record.id] = reason
    asof.mark_unreadable(record, reason)
    return None


def read_cached[T](asof: AsOf, record: RawRecord, cache: dict[str, T], parse: Callable[[bytes], T]) -> T | None:
    """`read_parsed` с кешем разобранных записей по `raw_id`."""
    if record.id not in cache:
        value = read_parsed(asof, record, parse)
        if value is None:
            return None
        cache[record.id] = value
    return cache[record.id]


def unreadable_records(asof: AsOf, start: datetime, end: datetime) -> list[UnreadableRecord]:
    """Записи, отвергнутые разбором при чтении слоя: доступность в `[start − 7 суток, end]` и не позже `asof.t`.

    Глубина в семь суток покрывает файлы, опубликованные до периода и влияющие на него; запись без известного
    времени доступности к периоду не отнесена и в список не попадает.
    """
    found: list[tuple[datetime, UnreadableRecord]] = []
    for item in asof.unreadable.values():
        at = item.available_at
        if at is None or (asof.t is not None and at > asof.t):
            continue
        if start - UNREADABLE_LOOKBACK <= at <= end:
            found.append((at, item))
    return [item for _, item in sorted(found, key=lambda pair: (pair[0], pair[1].raw_id))]


def _oem(asof: AsOf, record: RawRecord) -> OemEphemeris | None:
    return read_cached(asof, record, _OEM_CACHE, lambda content: parse_oem(content.decode("utf-8")))


def oem_for(asof: AsOf, window: Window) -> tuple[OemEphemeris, RawRecord] | None:
    need_start, need_end = window.start - _OEM_MARGIN, window.end + _OEM_MARGIN
    for rec in sorted(asof.records("nasa_oem", "oem"), key=availability_key, reverse=True):
        eph = _oem(asof, rec)
        if eph is not None and eph.header.start_time <= need_start and eph.header.stop_time >= need_end:
            return eph, rec
    return None


@dataclass(frozen=True)
class ProtonSeries:
    times: list[datetime]
    p10: NDArray[np.float64]
    p30: NDArray[np.float64]
    p50: NDArray[np.float64]
    p100: NDArray[np.float64]
    energies_mev: NDArray[np.float64]
    diff_flux: NDArray[np.float64]
    kind: Kind
    provenance: tuple[Provenance, ...]
    data_version: str
    note: str


def _read_sgps_bytes(content: bytes) -> SgpsDay:
    with NamedTemporaryFile(suffix=".nc", delete=False) as fh:
        fh.write(content)
        path = Path(fh.name)
    try:
        return read_sgps(path)
    finally:
        path.unlink(missing_ok=True)


def _sgps(asof: AsOf, record: RawRecord) -> SgpsDay | None:
    return read_cached(asof, record, _SGPS_CACHE, _read_sgps_bytes)


def _sensor_mean(flux: NDArray[np.float64]) -> NDArray[np.float64]:
    valid = ~np.isnan(flux)
    count = valid.sum(axis=1)
    total = np.where(valid, flux, 0.0).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = total / count
    return np.asarray(np.where(count > 0, mean, np.nan), dtype=np.float64)


def _latest_per_day(asof: AsOf, records: list[RawRecord]) -> list[tuple[RawRecord, SgpsDay]]:
    chosen: dict[date, tuple[RawRecord, SgpsDay]] = {}
    for rec in sorted(records, key=availability_key, reverse=True):
        day = _sgps(asof, rec)
        if day is None:
            continue
        chosen.setdefault(day.times[0].date(), (rec, day))
    return sorted(chosen.values(), key=lambda pair: pair[1].times[0])


def _goes_day_near(record: RawRecord, start: datetime, end: datetime) -> bool:
    m = _GOES_FILE_DAY.search(record.url)
    if m is None:
        return True
    ymd = m.group(1)
    day = date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))
    return (start - GOES_DAY_MARGIN).date() <= day <= (end + GOES_DAY_MARGIN).date()


def _goes_series(asof: AsOf, start: datetime, end: datetime) -> tuple[ProtonSeries, set[date]] | None:
    """Ряд из суточных файлов GOES SGPS и множество суток, которые он покрывает.

    Читаются только суточные файлы за сутки периода с запасом; запас периода задаёт вызывающий код. Сутки
    файла берутся из имени в url (`sci_sgps-l2-avg5m_g18_d20240510_v3-0-2.nc`), а не из доступности: у
    переобработанного файла доступность позже суток наблюдения. Имя без суток оставляет запись в чтении.
    """
    records = [r for r in asof.records("goes_sgps", "sgps_l2_avg5m") if _goes_day_near(r, start, end)]
    if not records:
        return None
    times: list[datetime] = []
    cols: dict[float, list[NDArray[np.float64]]] = {thr: [] for thr in _THRESHOLDS_MEV}
    spectra: list[NDArray[np.float64]] = []
    energies: NDArray[np.float64] | None = None
    used: list[Provenance] = []
    versions: set[str] = set()
    days: set[date] = set()
    for rec, day in _latest_per_day(asof, records):
        mask = np.asarray([start <= t < end for t in day.times], dtype=bool)
        if not mask.any():
            continue
        times.extend(t for t, m in zip(day.times, mask, strict=True) if m)
        for thr in _THRESHOLDS_MEV:
            cols[thr].append(integral_flux_pfu(day, thr)[mask])
        spectra.append(_sensor_mean(day.diff_flux[mask]) * _KEV_PER_MEV)
        energies = np.sqrt(day.lower_kev * day.upper_kev).mean(axis=0) / _KEV_PER_MEV
        used.append(provenance_of(rec))
        versions.add(rec.data_version)
        days.add(day.times[0].date())
    if not times or energies is None:
        return None
    series = ProtonSeries(
        times=times,
        p10=np.concatenate(cols[10.0]),
        p30=np.concatenate(cols[30.0]),
        p50=np.concatenate(cols[50.0]),
        p100=np.concatenate(cols[100.0]),
        energies_mev=energies,
        diff_flux=np.concatenate(spectra),
        kind="computed",
        provenance=tuple(used),
        data_version=", ".join(sorted(versions)),
        note=PROTON_NOTE,
    )
    return series, days


@dataclass(frozen=True)
class _LiveProtons:
    times: list[datetime]
    flux: NDArray[np.float64]


_LIVE_CACHE: dict[str, _LiveProtons] = {}


def _live_protons(content: bytes) -> _LiveProtons:
    by_time: dict[datetime, dict[float, float]] = {}
    for row in parse_integral_protons(content):
        if row.energy_mev in _THRESHOLDS_MEV:
            by_time.setdefault(row.time, {})[row.energy_mev] = row.flux_pfu
    times = sorted(by_time)
    flux = np.full((len(times), len(_THRESHOLDS_MEV)), np.nan, dtype=np.float64)
    for i, t in enumerate(times):
        for j, thr in enumerate(_THRESHOLDS_MEV):
            if thr in by_time[t]:
                flux[i, j] = by_time[t][thr]
    return _LiveProtons(times=times, flux=flux)


def _live(asof: AsOf, record: RawRecord) -> _LiveProtons | None:
    """Запись `protons_7d` как отсчёты по времени и столбцы p10/p30/p50/p100; `NaN` при отсутствии энергии."""
    return read_cached(asof, record, _LIVE_CACHE, _live_protons)


def _live_series(asof: AsOf, start: datetime, end: datetime) -> ProtonSeries | None:
    """Ряд из записей `swpc_live/protons_7d`: поздняя запись перекрывает раннюю по `time_tag`.

    Записи читаются от поздней к ранней; запись, чей первый отсчёт не позже `start`, покрывает весь интервал,
    и более ранние записи не читаются. Записи, полученные до `start`, отсчётов в интервале не содержат.
    """
    records = [r for r in asof.records(LIVE_SOURCE, LIVE_PROTONS) if r.fetched_at >= start]
    chosen: dict[datetime, NDArray[np.float64]] = {}
    used: list[Provenance] = []
    for rec in sorted(records, key=availability_key, reverse=True):
        live = _live(asof, rec)
        if live is None:
            continue
        added = False
        for i, t in enumerate(live.times):
            if start <= t < end and t not in chosen:
                chosen[t] = live.flux[i]
                added = True
        if added:
            used.append(provenance_of(rec))
        if live.times and live.times[0] <= start:
            break
    if not chosen:
        return None
    times = sorted(chosen)
    flux = np.vstack([chosen[t] for t in times])
    return ProtonSeries(
        times=times,
        p10=flux[:, 0],
        p30=flux[:, 1],
        p50=flux[:, 2],
        p100=flux[:, 3],
        energies_mev=np.empty((0,), dtype=np.float64),
        diff_flux=np.empty((len(times), 0), dtype=np.float64),
        kind="observation",
        provenance=tuple(reversed(used)),
        data_version=LIVE_PROTON_VERSION,
        note=LIVE_PROTON_NOTE,
    )


def _merge(goes: ProtonSeries, covered: set[date], live: ProtonSeries) -> ProtonSeries:
    """GOES за покрытые сутки, живые отсчёты за остальные; у живых отсчётов спектра нет, строки `NaN`."""
    keep = [i for i, t in enumerate(live.times) if t.date() not in covered]
    if not keep:
        return goes
    times = [*goes.times, *(live.times[i] for i in keep)]
    order = sorted(range(len(times)), key=lambda i: times[i])

    def column(a: NDArray[np.float64], b: NDArray[np.float64]) -> NDArray[np.float64]:
        return np.asarray(np.concatenate([a, b[keep]])[order], dtype=np.float64)

    no_spectrum = np.full((len(keep), goes.energies_mev.size), np.nan, dtype=np.float64)
    days = ", ".join(d.isoformat() for d in sorted(covered))
    return ProtonSeries(
        times=[times[i] for i in order],
        p10=column(goes.p10, live.p10),
        p30=column(goes.p30, live.p30),
        p50=column(goes.p50, live.p50),
        p100=column(goes.p100, live.p100),
        energies_mev=goes.energies_mev,
        diff_flux=np.asarray(np.vstack([goes.diff_flux, no_spectrum])[order], dtype=np.float64),
        kind="computed",
        provenance=(*goes.provenance, *live.provenance),
        data_version=", ".join(sorted({*goes.data_version.split(", "), live.data_version})),
        note=f"отсчёты за {days}: {goes.note}; остальные отсчёты: {live.note}",
    )


def proton_series(asof: AsOf, start: datetime, end: datetime) -> ProtonSeries | None:
    goes = _goes_series(asof, start, end)
    live = _live_series(asof, start, end)
    if goes is None:
        return live
    if live is None:
        return goes[0]
    return _merge(goes[0], goes[1], live)


@dataclass(frozen=True)
class KpSeries:
    starts: list[datetime]
    kp: NDArray[np.float64]
    kind: Kind
    provenance: tuple[Provenance, ...]
    data_version: str
    notes: tuple[str, ...] = ()


def kp_series(asof: AsOf, start: datetime, end: datetime) -> KpSeries | None:
    for rec in sorted(asof.records("gfz_kp", "kp"), key=availability_key, reverse=True):
        parsed = read_parsed(asof, rec, parse_kp_json)
        if parsed is None:
            continue
        values = [v for v in parsed if start - _KP_STEP <= v.start <= end]
        if values:
            return KpSeries(
                starts=[v.start for v in values],
                kp=np.asarray([v.kp for v in values], dtype=np.float64),
                kind="observation",
                provenance=(provenance_of(rec),),
                data_version=rec.data_version,
            )
    return None
