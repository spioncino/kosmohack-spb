from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING

from evarisk.parsers.donki import parse_notifications
from evarisk.parsers.swpc_text import issued_at
from evarisk.risk.config import SepConfig, load_sep_config
from evarisk.risk.lines.sep import env_level
from evarisk.store.asof import AsOf
from evarisk.store.raw import RawStore
from evarisk.store.views import message_provenance, proton_series, provenance_of
from evarisk.store.views_kp import proton_event_probability
from evarisk.types import (
    Availability,
    Confidence,
    Frozen,
    Interval,
    Level,
    LineAssessment,
    Provenance,
    Recommendation,
    Window,
    WindowScore,
)
from evarisk.windows.compare import WindowRules, candidates, load_window_rules, recommend, score

if TYPE_CHECKING:
    from evarisk.api.schemas import AssessmentRequest

PERSISTENCE = "persistence"
SINGLE_ALERT = "single_alert"
SWPC_OUTLOOK = "swpc_outlook"
TITLES = {
    PERSISTENCE: "персистентность",
    SINGLE_ALERT: "единственное предупреждение",
    SWPC_OUTLOOK: "прогноз SWPC без орбиты",
}
LINE = "sep_protons"
MECHANISM = "radiation_sep"
# Суточные файлы GOES появляются в архиве NCEI через 6–7 суток (D69): последнее доступное на момент отсечения
# наблюдение ищется за 10 суток, его давность пишется в основание бейзлайна.
PERSISTENCE_LOOKBACK = timedelta(days=10)
SWPC_SOURCE = "swpc_archive"
THREE_DAY = "3day_forecast"
DONKI_SOURCE = "donki"
DONKI_PRODUCT = "notifications"
SEP_TYPE = "SEP"
_S_SCALE_OBSERVED = re.compile(r"over the past 24 hours,\s+was\s+(above|below)\s+S-scale storm level thresholds", re.S)
_GOES_S1 = re.compile(r"GOES.{0,120}?>\s*10 MeV protons exceeds 10 pfu", re.S)
RULE_PERSISTENCE = (
    "бейзлайн «персистентность»: уровень окружения по шкале S последнего доступного на момент отсечения отсчёта p10 "
    "распространён на весь период поиска; доступ частиц по траектории не учитывается (D41)"
)
RULE_SINGLE_ALERT = (
    "бейзлайн «единственное предупреждение»: последнее на момент отсечения утверждение о шкале S (строка "
    "3-day forecast об S-scale за прошедшие 24 ч или сообщение DONKI SEP о превышении 10 pfu по ≥10 MeV на GOES); "
    "S ≥ 1 даёт elevated всему периоду, иначе none (D41)"
)
RULE_SWPC_OUTLOOK = (
    "бейзлайн «прогноз SWPC без орбиты»: вероятность протонного события S1+ из последнего на момент отсечения "
    "выпуска daypre или 3-day forecast на каждые сутки периода; не ниже порога {threshold:g} % даёт elevated всем "
    "суткам, ниже порога none; траектория и доступ частиц не учитываются (D41)"
)
NOTE_NO_SAMPLE = "нет отсчёта p10, доступного на момент отсечения"
NOTE_NO_STATEMENT = "нет утверждения о шкале S, доступного на момент отсечения"
NOTE_NO_OUTLOOK = "нет выпуска с вероятностью протонного события на эти сутки, доступного на момент отсечения"
_KNOWN_ORDER: tuple[Level, ...] = ("none", "elevated", "high", "critical")
NOTE_NO_EXPIRY = (
    "у сообщения DONKI нет отбоя: утверждение действует, пока его не сменит более поздний выпуск 3-day forecast"
)


class Baseline(Frozen):
    name: str
    title: str
    level: Level
    basis: str
    provenance: tuple[Provenance, ...]
    line: LineAssessment
    windows: list[WindowScore]
    recommendation: Recommendation


@dataclass(frozen=True)
class Statement:
    issued_at: datetime
    above: bool
    provenance: Provenance
    text: str


def _cutoff(req: AssessmentRequest) -> datetime:
    if req.cutoff is None:
        raise ValueError("бейзлайн требует cutoff режима replay")
    return req.cutoff


def _assessment(
    req: AssessmentRequest,
    level: Level,
    availability: Availability,
    provenance: tuple[Provenance, ...],
    rule: str,
    note: str,
    limitations: tuple[str, ...],
) -> LineAssessment:
    interval = Interval(
        start=req.search_start,
        end=req.search_end,
        level=level,
        kind="computed",
        provenance=provenance,
        rule=rule,
        note=note,
    )
    return LineAssessment(
        line=LINE,
        mechanism=MECHANISM,
        intervals=(interval,),
        availability=availability,
        confidence=Confidence(grade="low", depends_on="бейзлайн: один признак на весь период поиска"),
        limitations=limitations,
    )


def _rank(
    req: AssessmentRequest,
    line: LineAssessment,
    rules: WindowRules,
    others: tuple[LineAssessment, ...],
    illumination: LineAssessment | None = None,
) -> tuple[list[WindowScore], Recommendation]:
    """Окна бейзлайна по тем же правилам и ограничениям запроса, что у расчёта; линия SEP заменена бейзлайном."""
    duration = timedelta(hours=req.duration_hours)
    lines = [line, *others]
    scores = [
        score(w, lines, rules, constraints=req.constraints, illumination=illumination, cutoff=req.cutoff)
        for w in candidates(req.search_start, req.search_end, duration, rules)
    ]
    requested = score(
        Window(start=req.eva_start, end=req.eva_start + duration),
        lines,
        rules,
        constraints=req.constraints,
        illumination=illumination,
    )
    return scores, recommend(scores, rules, requested=requested)


def _age_text(cutoff: datetime, moment: datetime) -> str:
    return f"давность {(cutoff - moment).total_seconds() / 3600.0:.1f} ч"


def persistence(
    req: AssessmentRequest,
    store: RawStore,
    *,
    cfg: SepConfig | None = None,
    rules: WindowRules | None = None,
    other_lines: tuple[LineAssessment, ...] = (),
    illumination: LineAssessment | None = None,
) -> Baseline:
    cutoff = _cutoff(req)
    cfg = cfg or load_sep_config()
    rules = rules or load_window_rules()
    asof = AsOf(store, cutoff)
    protons = proton_series(asof, cutoff - PERSISTENCE_LOOKBACK, cutoff + timedelta(minutes=1))
    last: tuple[datetime, float] | None = None
    if protons is not None:
        finite = [(t, float(v)) for t, v in zip(protons.times, protons.p10, strict=True) if t <= cutoff]
        finite = [(t, v) for t, v in finite if not math.isnan(v)]
        if finite:
            last = finite[-1]
    if last is None:
        line = _assessment(req, "unknown", "missing", (), RULE_PERSISTENCE, NOTE_NO_SAMPLE, (NOTE_NO_SAMPLE,))
        windows, rec = _rank(req, line, rules, other_lines, illumination)
        return Baseline(
            name=PERSISTENCE,
            title=TITLES[PERSISTENCE],
            level="unknown",
            basis=NOTE_NO_SAMPLE,
            provenance=(),
            line=line,
            windows=windows,
            recommendation=rec,
        )
    stamp, p10 = last
    level = env_level(p10, cfg)
    basis = f"последний доступный отсчёт p10 {p10:.2f} pfu в {stamp:%Y-%m-%d %H:%M} UTC, {_age_text(cutoff, stamp)}"
    provenance = protons.provenance if protons is not None else ()
    line = _assessment(
        req,
        level,
        "available",
        provenance,
        RULE_PERSISTENCE,
        basis,
        (f"персистентность: отсчёт {stamp:%Y-%m-%d %H:%M} UTC принят действующим на всём периоде поиска", basis),
    )
    windows, rec = _rank(req, line, rules, other_lines, illumination)
    return Baseline(
        name=PERSISTENCE,
        title=TITLES[PERSISTENCE],
        level=level,
        basis=basis,
        provenance=provenance,
        line=line,
        windows=windows,
        recommendation=rec,
    )


def _three_day_statement(asof: AsOf) -> Statement | None:
    rec = asof.latest(SWPC_SOURCE, THREE_DAY)
    if rec is None:
        return None
    text = asof.read(rec).decode("utf-8")
    m = _S_SCALE_OBSERVED.search(text)
    if m is None:
        raise ValueError(f"в выпуске 3-day forecast {rec.id} нет строки об S-scale за прошедшие 24 ч")
    return Statement(
        issued_at=issued_at(text),
        above=m.group(1) == "above",
        provenance=provenance_of(rec),
        text=f"3-day forecast {issued_at(text):%Y-%m-%d %H:%M} UTC: {m.group(1)} S-scale storm level thresholds",
    )


def _donki_statement(asof: AsOf, cutoff: datetime) -> Statement | None:
    found: list[Statement] = []
    for rec in asof.aggregates(DONKI_SOURCE, DONKI_PRODUCT):
        for note in parse_notifications(asof.read(rec)):
            if note.message_type != SEP_TYPE or note.issued_at > cutoff or _GOES_S1.search(note.body) is None:
                continue
            found.append(
                Statement(
                    issued_at=note.issued_at,
                    above=True,
                    provenance=message_provenance(rec, note.issued_at, note.message_id),
                    text=f"DONKI SEP {note.message_id} {note.issued_at:%Y-%m-%d %H:%M} UTC: GOES >10 MeV выше 10 pfu",
                )
            )
    return max(found, key=lambda s: s.issued_at) if found else None


def single_alert(
    req: AssessmentRequest,
    store: RawStore,
    *,
    rules: WindowRules | None = None,
    other_lines: tuple[LineAssessment, ...] = (),
    illumination: LineAssessment | None = None,
) -> Baseline:
    cutoff = _cutoff(req)
    rules = rules or load_window_rules()
    asof = AsOf(store, cutoff)
    statements = [s for s in (_three_day_statement(asof), _donki_statement(asof, cutoff)) if s is not None]
    if not statements:
        line = _assessment(req, "unknown", "missing", (), RULE_SINGLE_ALERT, NOTE_NO_STATEMENT, (NOTE_NO_STATEMENT,))
        windows, rec = _rank(req, line, rules, other_lines, illumination)
        return Baseline(
            name=SINGLE_ALERT,
            title=TITLES[SINGLE_ALERT],
            level="unknown",
            basis=NOTE_NO_STATEMENT,
            provenance=(),
            line=line,
            windows=windows,
            recommendation=rec,
        )
    latest = max(statements, key=lambda s: s.issued_at)
    level: Level = "elevated" if latest.above else "none"
    basis = f"{latest.text}, {_age_text(cutoff, latest.issued_at)}"
    limitations = [basis]
    if latest.provenance.source == DONKI_SOURCE:
        limitations.append(NOTE_NO_EXPIRY)
    line = _assessment(req, level, "available", (latest.provenance,), RULE_SINGLE_ALERT, basis, tuple(limitations))
    windows, rec = _rank(req, line, rules, other_lines, illumination)
    return Baseline(
        name=SINGLE_ALERT,
        title=TITLES[SINGLE_ALERT],
        level=level,
        basis=basis,
        provenance=(latest.provenance,),
        line=line,
        windows=windows,
        recommendation=rec,
    )


def _highest(levels: list[Level]) -> Level:
    known = [lv for lv in levels if lv in _KNOWN_ORDER]
    return max(known, key=_KNOWN_ORDER.index) if known else "unknown"


def swpc_outlook(
    req: AssessmentRequest,
    store: RawStore,
    *,
    cfg: SepConfig | None = None,
    rules: WindowRules | None = None,
    other_lines: tuple[LineAssessment, ...] = (),
    illumination: LineAssessment | None = None,
) -> Baseline:
    """Внешний прогноз вероятности протонного события как есть: одно значение на сутки, без траектории.

    Это то же основание, которым пользуется линия SEP за горизонтом персистентности (D42), но без минут
    геомагнитного доступа: бейзлайн показывает, что даёт привязка прогноза к виткам станции.
    """
    cutoff = _cutoff(req)
    cfg = cfg or load_sep_config()
    rules = rules or load_window_rules()
    asof = AsOf(store, cutoff)
    threshold = cfg.proton_outlook_threshold_percent
    rule = RULE_SWPC_OUTLOOK.format(threshold=threshold)
    intervals: list[Interval] = []
    provenance: list[Provenance] = []
    notes: list[str] = []
    day_start = datetime.combine(req.search_start.date(), time(0), tzinfo=UTC)
    while day_start < req.search_end:
        start, end = max(req.search_start, day_start), min(req.search_end, day_start + timedelta(days=1))
        found = proton_event_probability(asof, day_start.date())
        if found is None:
            note = f"{day_start:%Y-%m-%d}: {NOTE_NO_OUTLOOK}"
            intervals.append(
                Interval(start=start, end=end, level="unknown", kind="computed", provenance=(), rule=rule, note=note)
            )
        else:
            percent, prov = found
            level: Level = "elevated" if percent >= threshold else "none"
            issued = "время выпуска неизвестно" if prov.published_at is None else _age_text(cutoff, prov.published_at)
            note = f"{day_start:%Y-%m-%d}: вероятность {percent} % ({prov.product}, {issued})"
            intervals.append(
                Interval(
                    start=start,
                    end=end,
                    level=level,
                    kind="external_forecast",
                    provenance=(prov,),
                    rule=rule,
                    note=note,
                )
            )
            provenance.append(prov)
        notes.append(note)
        day_start += timedelta(days=1)
    basis = "; ".join(notes)
    availability: Availability = "available" if provenance else "missing"
    line = LineAssessment(
        line=LINE,
        mechanism=MECHANISM,
        intervals=tuple(intervals),
        availability=availability,
        confidence=Confidence(grade="low", depends_on="бейзлайн: вероятность на сутки без привязки к траектории"),
        limitations=(basis,),
    )
    windows, rec = _rank(req, line, rules, other_lines, illumination)
    unique = tuple(dict.fromkeys(provenance))
    return Baseline(
        name=SWPC_OUTLOOK,
        title=TITLES[SWPC_OUTLOOK],
        level=_highest([iv.level for iv in intervals]),
        basis=basis,
        provenance=unique,
        line=line,
        windows=windows,
        recommendation=rec,
    )
