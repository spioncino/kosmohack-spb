from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from evarisk.parsers.donki import (
    CmeArrival,
    SepCrossing,
    SepEnd,
    Unparsed,
    cme_arrivals,
    parse_notifications,
    sep_crossings,
    sep_ends,
    tolerant,
)
from evarisk.store.asof import AsOf
from evarisk.store.raw import RawRecord
from evarisk.store.views import KpSeries, message_provenance, read_parsed
from evarisk.store.views_kp import gst_skipped
from evarisk.types import Provenance

DONKI_SOURCE = "donki"
DONKI_PRODUCT = "notifications"
DONKI_VERSION = "donki notifications"
P10_MEV = 10.0
P100_MEV = 100.0
KP_STEP = timedelta(hours=3)
SKIPPED_LOOKBACK = timedelta(days=7)
NOTE_CME = (
    "прогноз прихода CME, DONKI/WSA-ENLIL, выпуск {issued} UTC, приход {arrival} ± {hours:g} ч, "
    "Kp {kp_min:g}–{kp_max:g}"
)

_CACHE: dict[str, "_Parsed"] = {}


@dataclass(frozen=True)
class _Parsed:
    crossings: list[SepCrossing]
    ends: list[SepEnd]
    arrivals: list[CmeArrival]
    skipped: list[Unparsed]


@dataclass(frozen=True)
class SepEventKnown:
    """Событие по уведомлениям DONKI, известное на момент запроса: пересечение порога GOES без известного окончания.

    `provenance` это сообщения, которые задают событие: самое раннее о начале (`issued_at`) и самое позднее
    подтверждение (`confirmed_at`), если это другое сообщение (D81).
    """

    energy_mev: float
    threshold_pfu: float
    since: datetime
    issued_at: datetime
    confirmed_at: datetime
    message_id: str
    provenance: tuple[Provenance, ...] = ()


@dataclass(frozen=True)
class SepStateKnown:
    """Состояние протонного события по DONKI на отрезке `[start, end)`; хотя бы одно из событий известно.

    `provenance` это сообщения обоих событий отрезка (`SepEventKnown.provenance`), а не все сообщения архива (D81).
    """

    start: datetime
    end: datetime
    p10_event: SepEventKnown | None
    p100_event: SepEventKnown | None
    provenance: tuple[Provenance, ...]
    data_version: str

    @property
    def p10_event_since(self) -> datetime | None:
        return None if self.p10_event is None else self.p10_event.since

    @property
    def p100_event_since(self) -> datetime | None:
        return None if self.p100_event is None else self.p100_event.since


@dataclass(frozen=True)
class _ArrivalKp:
    """Прогноз прихода CME с известным диапазоном Kp и провенансом записи."""

    arrival: datetime
    uncertainty: timedelta
    kp_min: float
    kp_max: float
    issued_at: datetime
    provenance: Provenance


@dataclass
class _Known:
    energy_mev: float
    threshold_pfu: float
    start: datetime
    issued_at: datetime
    confirmed_at: datetime
    message_id: str
    issued_provenance: Provenance
    confirmed_provenance: Provenance

    def provenance(self) -> tuple[Provenance, ...]:
        if self.confirmed_provenance.data_version == self.issued_provenance.data_version:
            return (self.issued_provenance,)
        return (self.issued_provenance, self.confirmed_provenance)


def _parsed(asof: AsOf, rec: RawRecord) -> _Parsed:
    if rec.id not in _CACHE:
        # Файл уведомлений, который не разбирается целиком, пропускается и называется в unreadable_records.
        notes = read_parsed(asof, rec, parse_notifications) or []
        skipped: list[Unparsed] = []
        _CACHE[rec.id] = _Parsed(
            crossings=tolerant(sep_crossings, notes, skipped),
            ends=tolerant(sep_ends, notes, skipped),
            arrivals=tolerant(cme_arrivals, notes, skipped),
            skipped=skipped,
        )
    return _CACHE[rec.id]


def skipped_messages(asof: AsOf, start: datetime, end: datetime) -> list[Unparsed]:
    """Сообщения DONKI, отвергнутые разбором и пропущенные: выпуск не позже `asof.t` и в `[start − 7 суток, end]`.

    Глубина в семь суток покрывает события и бури, начавшиеся до периода и влияющие на него; более старые
    сообщения к периоду не относятся и в ограничения не попадают.
    """
    found: dict[str, Unparsed] = {}
    for rec in asof.aggregates(DONKI_SOURCE, DONKI_PRODUCT):
        for item in [*_parsed(asof, rec).skipped, *gst_skipped(asof, rec)]:
            if asof.t is not None and item.issued_at > asof.t:
                continue
            if start - SKIPPED_LOOKBACK <= item.issued_at <= end:
                found.setdefault(item.message_id, item)
    return sorted(found.values(), key=lambda u: (u.issued_at, u.message_id))


def _slot_floor(t: datetime) -> datetime:
    return t.replace(hour=t.hour - t.hour % 3, minute=0, second=0, microsecond=0)


def _known_events(asof: AsOf) -> tuple[dict[tuple[float, datetime], _Known], dict[tuple[float, datetime], SepEnd]]:
    """Пересечения и окончания из сообщений с выпуском не позже `asof.t`, слитые по (энергия, время).

    У каждого пересечения запоминаются сообщения, которые его задают: самое раннее о начале и самое позднее
    подтверждение к `asof.t`, каждое с записью, где оно лежит (D81).
    """
    t = asof.t
    crossings: dict[tuple[float, datetime], _Known] = {}
    ends: dict[tuple[float, datetime], SepEnd] = {}
    for rec in asof.aggregates(DONKI_SOURCE, DONKI_PRODUCT):
        parsed = _parsed(asof, rec)
        for c in parsed.crossings:
            confirmations = [
                (x, message_id)
                for x, message_id in zip(c.confirmations, c.confirmed_by, strict=True)
                if t is None or x <= t
            ]
            if not confirmations:
                continue
            (first_at, first_id), (last_at, last_id) = confirmations[0], confirmations[-1]
            key = (c.energy_mev, c.start)
            known = crossings.get(key)
            if known is None:
                crossings[key] = _Known(
                    energy_mev=c.energy_mev,
                    threshold_pfu=c.threshold_pfu,
                    start=c.start,
                    issued_at=first_at,
                    confirmed_at=last_at,
                    message_id=first_id,
                    issued_provenance=message_provenance(rec, first_at, first_id),
                    confirmed_provenance=message_provenance(rec, last_at, last_id),
                )
                continue
            if first_at < known.issued_at:
                known.issued_at, known.message_id = first_at, first_id
                known.issued_provenance = message_provenance(rec, first_at, first_id)
            if last_at > known.confirmed_at:
                known.confirmed_at = last_at
                known.confirmed_provenance = message_provenance(rec, last_at, last_id)
        for e in parsed.ends:
            if t is not None and e.issued_at > t:
                continue
            key = (e.energy_mev, e.time)
            if key not in ends or e.issued_at < ends[key].issued_at:
                ends[key] = e
    return crossings, ends


def _event_at(moment: datetime, crossings: list[_Known], ends: list[SepEnd]) -> SepEventKnown | None:
    current = [c for c in crossings if c.start <= moment]
    if not current:
        return None
    c = max(current, key=lambda k: k.start)
    if any(c.start < e.time <= moment for e in ends):
        return None
    return SepEventKnown(
        energy_mev=c.energy_mev,
        threshold_pfu=c.threshold_pfu,
        since=c.start,
        issued_at=c.issued_at,
        confirmed_at=c.confirmed_at,
        message_id=c.message_id,
        provenance=c.provenance(),
    )


def sep_state_as_known(asof: AsOf, start: datetime, end: datetime) -> list[SepStateKnown]:
    """Состояние событий ≥10 MeV > 10 pfu и ≥100 MeV > 1 pfu по DONKI на каждый момент `[start, end)`.

    Учитываются только сообщения с выпуском не позже `asof.t`; отрезки без известного события не возвращаются.
    """
    crossings, ends = _known_events(asof)
    by_energy = {
        energy: (
            [c for c in crossings.values() if c.energy_mev == energy],
            [e for e in ends.values() if e.energy_mev == energy],
        )
        for energy in (P10_MEV, P100_MEV)
    }
    boundaries = sorted({start, *(c.start for c in crossings.values()), *(e.time for e in ends.values())})
    pieces: list[SepStateKnown] = []
    for i, b in enumerate(boundaries):
        piece_start = max(b, start)
        piece_end = min(boundaries[i + 1], end) if i + 1 < len(boundaries) else end
        if piece_end <= piece_start:
            continue
        p10 = _event_at(b, *by_energy[P10_MEV])
        p100 = _event_at(b, *by_energy[P100_MEV])
        if p10 is None and p100 is None:
            continue
        if pieces and pieces[-1].end == piece_start and (pieces[-1].p10_event, pieces[-1].p100_event) == (p10, p100):
            last = pieces.pop()
            piece_start = last.start
        pieces.append(
            SepStateKnown(
                start=piece_start,
                end=piece_end,
                p10_event=p10,
                p100_event=p100,
                provenance=tuple(
                    dict.fromkeys(prov for event in (p10, p100) if event is not None for prov in event.provenance)
                ),
                data_version=DONKI_VERSION,
            )
        )
    return pieces


def cme_kp_forecast(asof: AsOf, start: datetime, end: datetime) -> KpSeries | None:
    """Kp по прогнозам прихода CME DONKI, выпущенным не позже `asof.t`: наибольший `kp_max` действующих прогнозов.

    Прогноз действует на 3-часовые интервалы, пересекающие `[arrival − uncertainty, arrival + uncertainty]`;
    прогнозы без диапазона Kp значения не дают; без прогнозов на запрошенный диапазон `None`.
    """
    t = asof.t
    arrivals: dict[str, _ArrivalKp] = {}
    for rec in asof.aggregates(DONKI_SOURCE, DONKI_PRODUCT):
        for a in _parsed(asof, rec).arrivals:
            if (t is not None and a.issued_at > t) or a.kp_max is None or a.kp_min is None:
                continue
            arrivals.setdefault(
                a.notification.message_id,
                _ArrivalKp(
                    arrival=a.arrival,
                    uncertainty=timedelta(hours=a.uncertainty_hours),
                    kp_min=a.kp_min,
                    kp_max=a.kp_max,
                    issued_at=a.issued_at,
                    provenance=message_provenance(rec, a.issued_at, a.notification.message_id),
                ),
            )
    if not arrivals:
        return None
    starts: list[datetime] = []
    values: list[float] = []
    notes: list[str] = []
    provenance: dict[str, Provenance] = {}
    slot = _slot_floor(start - KP_STEP)
    while slot <= end:
        active = [
            a
            for a in arrivals.values()
            if a.arrival - a.uncertainty < slot + KP_STEP and slot < a.arrival + a.uncertainty
        ]
        if active:
            best = max(active, key=lambda x: (x.kp_max, x.issued_at))
            starts.append(slot)
            values.append(best.kp_max)
            notes.append(
                NOTE_CME.format(
                    issued=f"{best.issued_at:%Y-%m-%d %H:%M}",
                    arrival=f"{best.arrival:%Y-%m-%d %H:%M}",
                    hours=best.uncertainty.total_seconds() / 3600.0,
                    kp_min=best.kp_min,
                    kp_max=best.kp_max,
                )
            )
            provenance.setdefault(best.provenance.raw_id, best.provenance)
        slot += KP_STEP
    if not starts:
        return None
    return KpSeries(
        starts=starts,
        kp=np.asarray(values, dtype=np.float64),
        kind="external_forecast",
        provenance=tuple(provenance.values()),
        data_version=DONKI_VERSION,
        notes=tuple(notes),
    )
