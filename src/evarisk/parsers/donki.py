import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from evarisk.timeutil import parse_utc

_FIELDS = ("messageID", "messageType", "messageIssueTime", "messageURL", "messageBody")
_GST_KP = re.compile(
    r"Kp index (?:ha[sd] )?reached level (\d+(?:\.\d+)?) \([a-z ]+\) during the synoptic period "
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z) to (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z)"
)
GST_TYPE = "GST"
SEP_TYPE = "SEP"
CME_TYPE = "CME"
SYNOPTIC_PERIOD = timedelta(hours=3)
GOES = "GOES"
# Пороги уведомлений DONKI по интегральным потокам GOES: >10 MeV при 10 pfu и >100 MeV при 1 pfu.
GOES_THRESHOLDS_PFU: dict[float, float] = {10.0: 10.0, 100.0: 1.0}
_TIME = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z"
_NUMBER = r"\d+(?:\.\d+)?"
_SEP_DETECTOR = re.compile(r"Solar energetic particle event detected by ([A-Za-z0-9 ,]+?)\.")
_SEP_CROSSING = re.compile(rf"> ({_NUMBER}) MeV protons exceeds ({_NUMBER}) pfu starting at ({_TIME})")
_SEP_DIP = re.compile(
    rf"> ({_NUMBER}) MeV proton flux dipped (?:just )?below ({_NUMBER}) pfu at ({_TIME})"
    rf"(?: and has again risen above the threshold of ({_NUMBER}) pfu at ({_TIME}))?"
)
_CME_ARRIVAL = re.compile(
    rf"(?:will|may|might) reach NASA missions near Earth at about ({_TIME}) \(plus minus ({_NUMBER}) hours?\)"
)
_CME_KP = re.compile(rf"expected range of the maximum Kp index (?:is|of|being) ({_NUMBER})-({_NUMBER})")


@dataclass(frozen=True)
class Notification:
    message_id: str
    message_type: str
    issued_at: datetime
    url: str
    body: str


@dataclass(frozen=True)
class GstKp:
    start: datetime
    end: datetime
    kp: float
    notification: Notification


@dataclass(frozen=True)
class SepCrossing:
    """Пересечение порога интегрального потока протонов вверх по сообщению DONKI SEP (только GOES).

    `issued_at` и `notification` это самое раннее сообщение о данном начале; `confirmations` это времена выпуска
    всех сообщений, повторивших то же начало, по возрастанию, первое равно `issued_at`; `confirmed_by` это
    идентификаторы этих сообщений в том же порядке (провенанс по сообщению, D81).
    """

    energy_mev: float
    threshold_pfu: float
    start: datetime
    detector: str
    issued_at: datetime
    notification: Notification
    confirmations: tuple[datetime, ...]
    confirmed_by: tuple[str, ...]


@dataclass(frozen=True)
class SepEnd:
    energy_mev: float
    threshold_pfu: float
    time: datetime
    issued_at: datetime
    notification: Notification


@dataclass(frozen=True)
class CmeArrival:
    """Прогноз прихода CME к Земле из сообщения DONKI CME; диапазон Kp отсутствует у части сообщений."""

    arrival: datetime
    uncertainty_hours: float
    kp_min: float | None
    kp_max: float | None
    issued_at: datetime
    notification: Notification


def _notification(row: object) -> Notification:
    if not isinstance(row, dict):
        raise ValueError(f"элемент ответа DONKI не объект: {row!r}")
    missing = [f for f in _FIELDS if f not in row]
    if missing:
        raise ValueError(f"в сообщении DONKI нет полей {missing}")
    return Notification(
        message_id=str(row["messageID"]),
        message_type=str(row["messageType"]),
        issued_at=parse_utc(str(row["messageIssueTime"])),
        url=str(row["messageURL"]),
        body=str(row["messageBody"]),
    )


@dataclass(frozen=True)
class Unparsed:
    """Сообщение, которое разбор отверг: оно пропущено, причина сохранена для ограничений результата."""

    message_id: str
    message_type: str
    issued_at: datetime
    reason: str


def tolerant[T](
    extract: Callable[[list[Notification]], list[T]], notes: list[Notification], skipped: list[Unparsed]
) -> list[T]:
    """Результат `extract` без сообщений, на которых он даёт `ValueError`; отвергнутые дописываются в `skipped`.

    Тексты DONKI пишут люди, формулировки меняются («has reached», «reached», «had reached»): одно нераспознанное
    сообщение не должно лишать расчёт остальных. Строгие функции разбора остаются строгими, терпимость здесь.
    """
    try:
        return extract(notes)
    except ValueError:
        pass
    readable: list[Notification] = []
    for note in notes:
        try:
            extract([note])
        except ValueError as exc:
            skipped.append(Unparsed(note.message_id, note.message_type, note.issued_at, str(exc)))
        else:
            readable.append(note)
    return extract(readable)


def parse_notifications(content: bytes) -> list[Notification]:
    try:
        rows = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"ответ DONKI не JSON: {exc}") from exc
    if not isinstance(rows, list):
        raise ValueError(f"ответ DONKI не список: {type(rows).__name__}")
    notes = [_notification(r) for r in rows]
    return sorted(notes, key=lambda n: (n.issued_at, n.message_id))


def gst_kp_observations(notes: list[Notification]) -> list[GstKp]:
    found: list[GstKp] = []
    for note in notes:
        if note.message_type != GST_TYPE:
            continue
        m = _GST_KP.search(note.body)
        if m is None:
            raise ValueError(f"в сообщении GST {note.message_id} нет строки о Kp и синоптическом периоде")
        start, end = parse_utc(m.group(2)), parse_utc(m.group(3))
        if end - start != SYNOPTIC_PERIOD:
            raise ValueError(f"синоптический период сообщения {note.message_id} не 3 часа: {start} – {end}")
        if start > note.issued_at:
            raise ValueError(f"период сообщения {note.message_id} начинается после его выпуска")
        found.append(GstKp(start=start, end=end, kp=float(m.group(1)), notification=note))
    return sorted(found, key=lambda g: (g.start, g.notification.issued_at, g.notification.message_id))


def _goes_detector(note: Notification) -> str:
    m = _SEP_DETECTOR.search(note.body)
    if m is None:
        raise ValueError(f"в сообщении SEP {note.message_id} нет строки о детекторе события")
    detector = m.group(1).strip()
    if GOES not in detector:
        raise ValueError(f"порог в pfu в сообщении SEP {note.message_id} отнесён не к GOES: {detector}")
    return detector


def _goes_threshold(note: Notification, energy: float, threshold: float) -> None:
    if GOES_THRESHOLDS_PFU.get(energy) != threshold:
        raise ValueError(
            f"неизвестный порог GOES в сообщении SEP {note.message_id}: >{energy:g} MeV, {threshold:g} pfu"
        )


def _not_after_issue(note: Notification, moment: datetime, what: str) -> None:
    if moment > note.issued_at:
        raise ValueError(f"{what} в сообщении {note.message_id} позже его выпуска: {moment}")


def sep_crossings(notes: list[Notification]) -> list[SepCrossing]:
    """Пересечения порогов GOES вверх: «> N MeV protons exceeds M pfu starting at» и «has again risen above»."""
    found: dict[tuple[float, datetime], list[tuple[Notification, float, str]]] = {}
    for note in notes:
        if note.message_type != SEP_TYPE:
            continue
        hits = [(m.group(1), m.group(2), m.group(3)) for m in _SEP_CROSSING.finditer(note.body)]
        for m in _SEP_DIP.finditer(note.body):
            if m.group(4) is not None:
                hits.append((m.group(1), m.group(4), m.group(5)))
        if not hits:
            continue
        detector = _goes_detector(note)
        for energy_text, threshold_text, start_text in hits:
            energy, threshold = float(energy_text), float(threshold_text)
            _goes_threshold(note, energy, threshold)
            start = parse_utc(start_text)
            _not_after_issue(note, start, "начало события")
            found.setdefault((energy, start), []).append((note, threshold, detector))
    result: list[SepCrossing] = []
    for (energy, start), items in found.items():
        items.sort(key=lambda item: (item[0].issued_at, item[0].message_id))
        first, threshold, detector = items[0]
        result.append(
            SepCrossing(
                energy_mev=energy,
                threshold_pfu=threshold,
                start=start,
                detector=detector,
                issued_at=first.issued_at,
                notification=first,
                confirmations=tuple(item[0].issued_at for item in items),
                confirmed_by=tuple(item[0].message_id for item in items),
            )
        )
    return sorted(result, key=lambda c: (c.start, c.energy_mev))


def sep_ends(notes: list[Notification]) -> list[SepEnd]:
    """Окончания событий по фразе «> N MeV proton flux dipped (just) below M pfu at»; дубликаты по (энергия, время)."""
    found: dict[tuple[float, datetime], SepEnd] = {}
    for note in notes:
        if note.message_type != SEP_TYPE:
            continue
        for m in _SEP_DIP.finditer(note.body):
            energy, threshold = float(m.group(1)), float(m.group(2))
            _goes_threshold(note, energy, threshold)
            moment = parse_utc(m.group(3))
            _not_after_issue(note, moment, "окончание события")
            key = (energy, moment)
            if key not in found or note.issued_at < found[key].issued_at:
                found[key] = SepEnd(energy, threshold, moment, note.issued_at, note)
    return sorted(found.values(), key=lambda e: (e.time, e.energy_mev))


def cme_arrivals(notes: list[Notification]) -> list[CmeArrival]:
    """Прогнозы прихода CME к Земле: «reach NASA missions near Earth at about T (plus minus N hours)».

    Диапазон Kp берётся из фразы «expected range of the maximum Kp index is A-B» того же сообщения; без неё
    `kp_min` и `kp_max` равны `None`. Ансамблевые сообщения («between about A and B») не разбираются.
    """
    found: list[CmeArrival] = []
    for note in notes:
        if note.message_type != CME_TYPE:
            continue
        arrivals = _CME_ARRIVAL.findall(note.body)
        if not arrivals:
            continue
        if len(arrivals) > 1:
            raise ValueError(f"в сообщении CME {note.message_id} несколько прогнозов прихода к Земле")
        kp_min: float | None = None
        kp_max: float | None = None
        kp = _CME_KP.search(note.body)
        if kp is not None:
            kp_min, kp_max = float(kp.group(1)), float(kp.group(2))
            if kp_min > kp_max:
                raise ValueError(f"диапазон Kp в сообщении CME {note.message_id} задан наоборот: {kp_min}-{kp_max}")
        found.append(
            CmeArrival(
                arrival=parse_utc(arrivals[0][0]),
                uncertainty_hours=float(arrivals[0][1]),
                kp_min=kp_min,
                kp_max=kp_max,
                issued_at=note.issued_at,
                notification=note,
            )
        )
    return sorted(found, key=lambda a: (a.issued_at, a.notification.message_id))
