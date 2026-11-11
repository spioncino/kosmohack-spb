from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

import numpy as np
from numpy.typing import NDArray

from evarisk.parsers.donki import GstKp, Unparsed, gst_kp_observations, parse_notifications, tolerant
from evarisk.parsers.swpc_daily import Daypre, GeomagForecast, parse_daypre, parse_geomag_forecast
from evarisk.parsers.swpc_json import KpForecastRow, parse_kp_1m, parse_kp_forecast
from evarisk.parsers.swpc_text import KpCell, ThreeDayForecast, parse_three_day_forecast
from evarisk.store.asof import AsOf, availability_key
from evarisk.store.raw import RawRecord
from evarisk.store.views import LIVE_SOURCE, KpSeries, message_provenance, provenance_of, read_cached, read_parsed
from evarisk.types import Kind, Provenance

SWPC_SOURCE = "swpc_archive"
THREE_DAY = "3day_forecast"
GEOMAG_FORECAST = "geomag_forecast"
DAYPRE = "daypre"
LIVE_KP_FORECAST = "kp_forecast"
LIVE_KP_1M = "kp_1m"
DONKI_SOURCE = "donki"
DONKI_PRODUCT = "notifications"
KP_STEP = timedelta(hours=3)
OBSERVED_SPAN = timedelta(hours=24)
SLOTS_PER_DAY = 8
KP_AS_KNOWN_VERSION = "swpc dated products"
LIVE_OBSERVED_VERSION = "swpc live observed"
KP_1M_VERSION = "estimated"
NOTE_ELAPSED_CELL = "прошедшая ячейка таблицы 3-day forecast, оценка наблюдённого Kp SWPC"
NOTE_DONKI_GST = "DONKI GST, Kp за синоптический период"
NOTE_LIVE_OBSERVED = "наблюдённый Kp SWPC из продукта kp_forecast (observed)"
NOTE_KP_1M_MAX = "оценочный Kp SWPC, максимум за интервал"
NOTE_MAX_24H = "максимум за 24 ч из 3-day forecast, не значение интервала"
FORECAST_MARGIN = timedelta(days=4)

_THREE_DAY_CACHE: dict[str, ThreeDayForecast] = {}
_GEOMAG_CACHE: dict[str, GeomagForecast] = {}
_DAYPRE_CACHE: dict[str, Daypre] = {}
_GST_CACHE: dict[str, list[GstKp]] = {}
_GST_SKIPPED: dict[str, list[Unparsed]] = {}
_KP_FORECAST_CACHE: dict[str, list[KpForecastRow]] = {}
_KP_1M_CACHE: dict[str, "_Minutes"] = {}


@dataclass(frozen=True)
class _Slot:
    kp: float
    provenance: Provenance
    note: str
    version: str = KP_AS_KNOWN_VERSION


@dataclass(frozen=True)
class _Minutes:
    times: list[datetime]
    kp: NDArray[np.float64]


def _three_day(asof: AsOf, rec: RawRecord) -> ThreeDayForecast | None:
    return read_cached(asof, rec, _THREE_DAY_CACHE, lambda c: parse_three_day_forecast(c.decode("utf-8")))


def _geomag(asof: AsOf, rec: RawRecord) -> GeomagForecast | None:
    return read_cached(asof, rec, _GEOMAG_CACHE, lambda c: parse_geomag_forecast(c.decode("utf-8")))


def _daypre(asof: AsOf, rec: RawRecord) -> Daypre | None:
    return read_cached(asof, rec, _DAYPRE_CACHE, lambda c: parse_daypre(c.decode("utf-8")))


def _within(records: list[RawRecord], lo: datetime, hi: datetime) -> list[RawRecord]:
    """Записи, доступные в `[lo, hi]`; запись без известного времени доступности остаётся в списке."""
    return [r for r in records if r.published_at is None or lo <= r.published_at <= hi]


def _gst(asof: AsOf, rec: RawRecord) -> list[GstKp]:
    if rec.id not in _GST_CACHE:
        skipped: list[Unparsed] = []
        # Файл уведомлений, который не разбирается целиком, пропускается и называется в unreadable_records.
        notes = read_parsed(asof, rec, parse_notifications) or []
        _GST_CACHE[rec.id] = tolerant(gst_kp_observations, notes, skipped)
        _GST_SKIPPED[rec.id] = skipped
    return _GST_CACHE[rec.id]


def gst_skipped(asof: AsOf, rec: RawRecord) -> list[Unparsed]:
    """Сообщения GST записи, отвергнутые разбором: они пропущены, а не остановили расчёт."""
    _gst(asof, rec)
    return _GST_SKIPPED[rec.id]


def _kp_forecast_rows(asof: AsOf, rec: RawRecord) -> list[KpForecastRow] | None:
    return read_cached(asof, rec, _KP_FORECAST_CACHE, parse_kp_forecast)


def _minutes_of(content: bytes) -> _Minutes:
    rows = sorted(parse_kp_1m(content), key=lambda r: r.time)
    return _Minutes(times=[r.time for r in rows], kp=np.asarray([r.estimated_kp for r in rows], dtype=np.float64))


def _kp_minutes(asof: AsOf, rec: RawRecord) -> _Minutes | None:
    return read_cached(asof, rec, _KP_1M_CACHE, _minutes_of)


def slot_start(cell: KpCell) -> datetime:
    return datetime.combine(cell.day, time(cell.start_hour), tzinfo=UTC)


def _floor_to_slot(t: datetime) -> datetime:
    return t.replace(hour=t.hour - t.hour % 3, minute=0, second=0, microsecond=0)


def _three_day_issues(asof: AsOf, start: datetime, end: datetime) -> list[tuple[RawRecord, ThreeDayForecast]]:
    """Выпуски 3-day forecast из архива NCEI и живого продукта SWPC в порядке доступности.

    Читаются выпуски с доступностью в `[start − 4 суток, end + 4 суток]`: ячейка выпуска покрывает трое суток
    от дня выпуска, а наибольший наблюдённый Kp относится к 24 ч до выпуска, поэтому выпуски вне запаса на
    интервалы периода не влияют. Нераспознанный выпуск пропускается и попадает в `views.unreadable_records`.
    """
    records = _within(
        asof.records(SWPC_SOURCE, THREE_DAY) + asof.records(LIVE_SOURCE, THREE_DAY),
        start - FORECAST_MARGIN,
        end + FORECAST_MARGIN,
    )
    found: list[tuple[RawRecord, ThreeDayForecast]] = []
    for rec in sorted(records, key=availability_key):
        forecast = _three_day(asof, rec)
        if forecast is not None:
            found.append((rec, forecast))
    return found


def _series(slots: dict[datetime, _Slot], start: datetime, end: datetime, kind: Kind) -> KpSeries | None:
    keep = sorted(s for s in slots if start - KP_STEP <= s <= end)
    if not keep:
        return None
    provenance: dict[str, Provenance] = {}
    for s in keep:
        provenance.setdefault(slots[s].provenance.raw_id, slots[s].provenance)
    return KpSeries(
        starts=keep,
        kp=np.asarray([slots[s].kp for s in keep], dtype=np.float64),
        kind=kind,
        provenance=tuple(provenance.values()),
        data_version=", ".join(sorted({slots[s].version for s in keep})),
        notes=tuple(slots[s].note for s in keep),
    )


def _live_records_backwards(asof: AsOf, product: str, earliest: datetime) -> list[RawRecord]:
    """Записи живого продукта от поздней к ранней; полученные раньше `earliest` отсчётов после него не содержат."""
    records = [r for r in asof.records(LIVE_SOURCE, product) if r.fetched_at >= earliest]
    return sorted(records, key=availability_key, reverse=True)


def _live_observed_slots(asof: AsOf, start: datetime, end: datetime) -> dict[datetime, _Slot]:
    """Строки `observed` продукта kp_forecast: поздний снимок перекрывает ранний."""
    earliest = start - KP_STEP
    slots: dict[datetime, _Slot] = {}
    for rec in _live_records_backwards(asof, LIVE_KP_FORECAST, earliest):
        prov = provenance_of(rec)
        rows = _kp_forecast_rows(asof, rec)
        if rows is None:
            continue
        observed = [row for row in rows if row.status == "observed"]
        for row in observed:
            if (asof.t is None or row.start <= asof.t) and earliest <= row.start <= end:
                slots.setdefault(row.start, _Slot(row.kp, prov, NOTE_LIVE_OBSERVED, LIVE_OBSERVED_VERSION))
        if observed and min(row.start for row in observed) <= earliest:
            break
    return slots


def _kp_1m_slots(asof: AsOf, start: datetime, end: datetime) -> dict[datetime, _Slot]:
    """Максимум оценочного Kp по минутам за 3-часовой интервал; поздний снимок перекрывает ранний по минуте."""
    earliest = start - KP_STEP
    minutes: dict[datetime, tuple[float, Provenance]] = {}
    for rec in _live_records_backwards(asof, LIVE_KP_1M, earliest):
        prov = provenance_of(rec)
        parsed = _kp_minutes(asof, rec)
        if parsed is None:
            continue
        for t, kp in zip(parsed.times, parsed.kp.tolist(), strict=True):
            if (asof.t is None or t <= asof.t) and earliest <= _floor_to_slot(t) <= end:
                minutes.setdefault(t, (float(kp), prov))
        if parsed.times and parsed.times[0] <= earliest:
            break
    best: dict[datetime, tuple[float, Provenance]] = {}
    for t in sorted(minutes):
        s = _floor_to_slot(t)
        if s not in best or minutes[t][0] > best[s][0]:
            best[s] = minutes[t]
    return {s: _Slot(kp, prov, NOTE_KP_1M_MAX, KP_1M_VERSION) for s, (kp, prov) in best.items()}


def kp_as_known(asof: AsOf, start: datetime, end: datetime) -> KpSeries | None:
    slots: dict[datetime, _Slot] = {}
    issues = _three_day_issues(asof, start, end)
    for rec, forecast in issues:
        prov = provenance_of(rec)
        for cell in forecast.kp:
            s = slot_start(cell)
            if s + KP_STEP <= forecast.issued_at:
                slots[s] = _Slot(cell.kp, prov, NOTE_ELAPSED_CELL)
    for rec in asof.aggregates(DONKI_SOURCE, DONKI_PRODUCT):
        for g in _gst(asof, rec):
            if asof.t is not None and g.notification.issued_at > asof.t:
                continue
            if g.start in slots and slots[g.start].note == NOTE_ELAPSED_CELL:
                continue
            prov = message_provenance(rec, g.notification.issued_at, g.notification.message_id)
            slots[g.start] = _Slot(g.kp, prov, NOTE_DONKI_GST)
    for s, slot in _live_observed_slots(asof, start, end).items():
        slots.setdefault(s, slot)
    for s, slot in _kp_1m_slots(asof, start, end).items():
        slots.setdefault(s, slot)
    for rec, forecast in reversed(issues):
        prov = provenance_of(rec)
        first = _floor_to_slot(forecast.issued_at) - OBSERVED_SPAN
        for k in range(SLOTS_PER_DAY):
            slots.setdefault(first + k * KP_STEP, _Slot(forecast.observed_max_kp_24h, prov, NOTE_MAX_24H))
    return _series(slots, start, end, "observation")


def _forecast_cells(asof: AsOf, rec: RawRecord) -> tuple[datetime, tuple[KpCell, ...]] | None:
    if rec.product == THREE_DAY:
        f = _three_day(asof, rec)
        return None if f is None else (f.issued_at, f.kp)
    g = _geomag(asof, rec)
    return None if g is None else (g.issued_at, g.kp)


def _live_forecast_slots(asof: AsOf, rec: RawRecord) -> dict[datetime, _Slot]:
    """Строки `estimated` и `predicted` снимка kp_forecast на интервалы, не прошедшие к моменту снимка.

    Статус `estimated` в продукте стоит и у ещё не начавшихся интервалов текущих суток, а его значение за текущий
    интервал совпадает с ячейкой 3-day forecast, поэтому такие строки это внешний прогноз, не наблюдение.
    """
    rows = _kp_forecast_rows(asof, rec)
    if rows is None:
        return {}
    snapshot = rec.fetched_at
    prov = provenance_of(rec)
    version = f"snapshot {snapshot:%Y-%m-%d %H:%M}"
    slots: dict[datetime, _Slot] = {}
    for row in rows:
        if row.status != "observed" and row.start + KP_STEP > snapshot:
            note = f"прогноз Kp SWPC kp_forecast ({row.status}), снимок {snapshot:%Y-%m-%d %H:%M} UTC"
            slots[row.start] = _Slot(row.kp, prov, note, version)
    return slots


def _forecast_slots(asof: AsOf, rec: RawRecord) -> dict[datetime, _Slot]:
    if rec.source == LIVE_SOURCE and rec.product == LIVE_KP_FORECAST:
        return _live_forecast_slots(asof, rec)
    found = _forecast_cells(asof, rec)
    if found is None:
        return {}
    issued, cells = found
    prov = provenance_of(rec)
    note = f"прогноз Kp SWPC {rec.product}, выпуск {issued:%Y-%m-%d %H:%M} UTC"
    version = f"issue {issued:%Y-%m-%d %H:%M}"
    return {slot_start(c): _Slot(c.kp, prov, note, version) for c in cells if slot_start(c) + KP_STEP > issued}


def _needed_slots(start: datetime, end: datetime) -> set[datetime]:
    first = _floor_to_slot(start) - KP_STEP
    count = int((end - first) // KP_STEP) + 1
    return {first + k * KP_STEP for k in range(count) if start - KP_STEP <= first + k * KP_STEP}


def kp_forecast(asof: AsOf, start: datetime, end: datetime, *, include_elapsed: bool = False) -> KpSeries | None:
    """Прогноз Kp по интервалам: самый свежий выпуск заполняет интервалы первым, пропуски берутся из более ранних.

    Снимок kp_forecast около полуночи UTC не содержит строк текущих суток, а текстовый 3-day forecast выходит
    позже снимка; без слияния окно ближайших часов оставалось бы без Kp при имеющемся прогнозе.
    """
    candidates = (
        asof.records(SWPC_SOURCE, THREE_DAY)
        + asof.records(SWPC_SOURCE, GEOMAG_FORECAST)
        + asof.records(LIVE_SOURCE, THREE_DAY)
        + asof.records(LIVE_SOURCE, LIVE_KP_FORECAST)
    )
    if not candidates:
        return None
    limit = None if include_elapsed else asof.t
    needed = {s for s in _needed_slots(start, end) if limit is None or s + KP_STEP > limit}
    slots: dict[datetime, _Slot] = {}
    for rec in sorted(candidates, key=availability_key, reverse=True):
        for s, slot in _forecast_slots(asof, rec).items():
            # Прогноз имеет смысл для интервалов, не истёкших к моменту данных; `include_elapsed` оставляет и
            # истёкшие: последний прогноз на интервал, по которому наблюдённого значения к отсечению ещё нет.
            if limit is None or s + KP_STEP > limit:
                slots.setdefault(s, slot)
        if needed <= set(slots):
            break
    return _series(slots, start, end, "external_forecast")


def proton_event_probability(asof: AsOf, day: date) -> tuple[int, Provenance] | None:
    """Вероятность протонного события на сутки `day` по самому свежему продукту, где эти сутки есть.

    Читаются продукты с доступностью в `[day − 4 суток, day + 5 суток]`: `daypre` и 3-day forecast называют
    трое суток от дня выпуска, так что продукт вне запаса этих суток не содержит.
    """
    start = datetime.combine(day, time(0), tzinfo=UTC)
    end = start + timedelta(days=1)
    found: list[tuple[RawRecord, int]] = []
    for rec in _within(asof.records(SWPC_SOURCE, DAYPRE), start - FORECAST_MARGIN, end + FORECAST_MARGIN):
        daypre = _daypre(asof, rec)
        if daypre is None:
            continue
        probability = daypre.proton_probability
        if day in probability:
            found.append((rec, probability[day]))
    for rec, forecast in _three_day_issues(asof, start, end):
        if day in forecast.s1_probability:
            found.append((rec, forecast.s1_probability[day]))
    if not found:
        return None
    rec, percent = max(found, key=lambda pair: availability_key(pair[0]))
    return percent, provenance_of(rec)
