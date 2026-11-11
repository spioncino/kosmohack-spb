from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from evarisk.orbit.trajectory import from_oem
from evarisk.replay.baseline import Baseline, persistence, single_alert, swpc_outlook
from evarisk.risk.config import load_sep_config
from evarisk.risk.lines.illumination import IlluminationLine
from evarisk.risk.lines.meteors import MeteorLine, load_meteor_config
from evarisk.risk.lines.sep import SepLine, exposed_times
from evarisk.store.asof import AsOf
from evarisk.store.raw import RawStore
from evarisk.store.views import oem_for, provenance_of
from evarisk.types import (
    Availability,
    Frozen,
    Interval,
    Level,
    LineAssessment,
    Provenance,
    Recommendation,
    Window,
    WindowScore,
)
from evarisk.windows.compare import candidates, load_window_rules, recommend, score

if TYPE_CHECKING:
    from evarisk.api.schemas import AssessmentRequest, AssessmentResult

SEP_LINE = "sep_protons"
ALARM_LEVELS: frozenset[Level] = frozenset({"high", "critical"})
WARNING_LEVELS: frozenset[Level] = frozenset({"elevated", "high", "critical"})
STEP = timedelta(minutes=1)
NOTE_RULE = "сравнение по минутам периода поиска, порог ≥ high, факт это тот же расчёт с AsOf(None) (D41)"
NOTE_NO_EFFECT = "сверка не влияет на рекомендацию (D5)"
# Первый отрезок прогноза уровня high и выше считается названным заранее, если он начался не позже первого факта
# плюс один виток станции: проходы доступа идут раз в полтора часа.
ALARM_MATCH = timedelta(minutes=95)
NOTE_NO_TRAJECTORY = "факт недоступен: нет эфемериды МКС, покрывающей период поиска"
NOTE_EXPOSURE = (
    "экспозиция экипажа, то есть минуты геомагнитного доступа с уровнем ≥ high: попаданий {hit:g} мин, пропусков "
    "{miss:g} мин, ложных тревог {false_alarm:g} мин; остальные минуты факта ≥ high это уровень обстановки на "
    "геостационарной орбите, станция на них закрыта магнитным полем"
)
NOTE_BASELINES = (
    "бейзлайны видят те же данные до отсечения, заменяют только линию SEP одним уровнем на период или сутки без "
    "траектории и сверяются с тем же фактом той же функцией (D41)"
)
NOTE_BASELINE_FAILED = "бейзлайн {name} не посчитан: {reason}"
# Линии, которые участвуют в ранжировании окон бейзлайна вместе с ним: те же, что в штрафе расчёта, кроме SEP.
BASELINE_OTHER_LINES = frozenset({"meteor_showers", "conjunctions"})
ILLUMINATION_LINE = "illumination"


class BaselineCheck(Frozen):
    """Бейзлайн на том же отсечении и его сверка с тем же фактом.

    Главный показатель для оператора `preferred_exposure_minutes`: сколько минут экспозиции экипажа по факту
    попало в окно, которое выбрал бы этот подход. Счёты попаданий, пропусков и ложных тревог идут по минутам
    экспозиции, как у расчёта: бейзлайн с уровнем high и выше называет экспозицией каждую минуту периода.
    """

    name: str
    title: str
    rule: str
    level: Level
    basis: str
    provenance: tuple[Provenance, ...]
    preferred: Window | None
    insufficient: bool
    preferred_exposure_minutes: float | None
    exposure_hit_minutes: float | None
    exposure_miss_minutes: float | None
    exposure_false_alarm_minutes: float | None
    lead_time_minutes: float | None
    window_choice_stable: bool
    note: str


class Verification(Frozen):
    truth_asof: None = None
    line: str = SEP_LINE
    predicted: tuple[Interval, ...]
    observed: tuple[Interval, ...]
    observed_availability: Availability
    observed_preferred: Window | None
    observed_insufficient: bool
    lead_time_minutes: float | None
    first_observed_alarm: datetime | None
    first_predicted_warning: datetime | None
    hit_minutes: float
    miss_minutes: float
    false_alarm_minutes: float
    quiet_minutes: float
    unverified_minutes: float
    predicted_unknown_minutes: float
    window_choice_stable: bool
    note: str
    first_predicted_alarm: datetime | None = None
    alarm_lead_time_minutes: float | None = None
    exposure_hit_minutes: float | None = None
    exposure_miss_minutes: float | None = None
    exposure_false_alarm_minutes: float | None = None
    requested_exposure_minutes: float | None = None
    preferred_exposure_minutes: float | None = None
    baselines: tuple[BaselineCheck, ...] = ()


@dataclass(frozen=True)
class MinuteCounts:
    hit: float
    miss: float
    false_alarm: float
    quiet: float
    unverified: float
    predicted_unknown: float


@dataclass(frozen=True)
class Observed:
    sep: LineAssessment
    exposed: frozenset[datetime]
    meteors: LineAssessment
    windows: list[WindowScore]
    recommendation: Recommendation


def level_at(intervals: tuple[Interval, ...], moment: datetime) -> Level:
    for iv in intervals:
        if iv.start <= moment < iv.end:
            return iv.level
    return "unknown"


def minute_counts(predicted: tuple[Interval, ...], observed: tuple[Interval, ...], span: Window) -> MinuteCounts:
    hit = miss = false_alarm = quiet = unverified = predicted_unknown = 0
    moment = span.start
    while moment < span.end:
        p, o = level_at(predicted, moment), level_at(observed, moment)
        if p == "unknown":
            predicted_unknown += 1
        if o == "unknown":
            unverified += 1
        elif p in ALARM_LEVELS and o in ALARM_LEVELS:
            hit += 1
        elif p in ALARM_LEVELS:
            false_alarm += 1
        elif o in ALARM_LEVELS:
            miss += 1
        else:
            quiet += 1
        moment += STEP
    minutes = STEP.total_seconds() / 60.0
    return MinuteCounts(
        hit=hit * minutes,
        miss=miss * minutes,
        false_alarm=false_alarm * minutes,
        quiet=quiet * minutes,
        unverified=unverified * minutes,
        predicted_unknown=predicted_unknown * minutes,
    )


def first_start(intervals: tuple[Interval, ...], levels: frozenset[Level], span: Window) -> datetime | None:
    starts = [
        max(iv.start, span.start)
        for iv in intervals
        if iv.level in levels and max(iv.start, span.start) < min(iv.end, span.end)
    ]
    return min(starts) if starts else None


def _stamp(moment: datetime) -> str:
    return f"{moment:%Y-%m-%d %H:%M} UTC"


def _window_text(window: Window | None) -> str:
    return "оснований для выбора нет" if window is None else f"{_stamp(window.start)} – {_stamp(window.end)}"


def compare(
    cutoff: datetime,
    predicted: tuple[Interval, ...],
    observed: tuple[Interval, ...],
    span: Window,
    *,
    predicted_preferred: Window | None,
    observed_preferred: Window | None,
    observed_insufficient: bool,
    observed_availability: Availability,
    line: str = SEP_LINE,
    notes: tuple[str, ...] = (),
    observed_equivalents: tuple[Window, ...] = (),
) -> Verification:
    counts = minute_counts(predicted, observed, span)
    first_alarm = first_start(observed, ALARM_LEVELS, span)
    first_warning = first_start(predicted, WARNING_LEVELS, span)
    lead: float | None = None
    remarks = [*notes]
    if observed_availability != "available":
        remarks.append(f"факт: данные линии {line} {observed_availability}; минуты без факта считаются непроверяемыми")
    if first_alarm is None:
        remarks.append("в периоде поиска факт не содержит интервалов ≥ high; заблаговременность не определена")
    elif first_warning is None:
        remarks.append(
            f"первый факт ≥ high в {_stamp(first_alarm)}, прогноз не дал ни elevated, ни high; "
            "заблаговременность не определена"
        )
    elif first_warning > first_alarm:
        remarks.append(
            f"первое предупреждение прогноза {_stamp(first_warning)} позже первого факта ≥ high "
            f"{_stamp(first_alarm)}; заблаговременность не определена"
        )
    elif cutoff >= first_alarm:
        remarks.append(
            f"первый факт ≥ high {_stamp(first_alarm)} начался не позже отсечения; заблаговременность не определена"
        )
    else:
        lead = (first_alarm - cutoff).total_seconds() / 60.0
        warned = level_at(predicted, first_warning)
        remarks.append(
            f"заблаговременность {lead:.0f} мин это время от отсечения {_stamp(cutoff)} до первого факта ≥ high "
            f"{_stamp(first_alarm)} при выданном заранее предупреждении ({warned}) с {_stamp(first_warning)}; "
            "она показывает, за сколько сервис предупредил, а не точность времени события"
        )
    first_predicted_alarm = first_start(predicted, ALARM_LEVELS, span)
    alarm_lead: float | None = None
    if first_alarm is not None and first_predicted_alarm is not None and cutoff < first_alarm:
        if first_predicted_alarm <= first_alarm + ALARM_MATCH:
            alarm_lead = (first_alarm - cutoff).total_seconds() / 60.0
            remarks.append(
                f"по уровню high и выше: прогноз назвал первый отрезок {_stamp(first_predicted_alarm)}, первый факт "
                f"{_stamp(first_alarm)}, заблаговременность {alarm_lead:.0f} мин"
            )
    exact = predicted_preferred == observed_preferred
    stable = exact or (predicted_preferred is not None and predicted_preferred in observed_equivalents)
    if predicted_preferred is None and observed_preferred is None:
        remarks.append("рекомендация совпала с фактом: в обоих случаях оснований для выбора нет")
    elif exact:
        remarks.append(f"рекомендация совпала с фактом: {_window_text(observed_preferred)}")
    elif stable:
        remarks.append(
            f"окно прогноза {_window_text(predicted_preferred)} входит в число равнозначных по факту (D79); "
            f"ближайшее к запрошенному по факту {_window_text(observed_preferred)} (D65)"
        )
    else:
        remarks.append(
            f"рекомендация не совпала с фактом: по прогнозу {_window_text(predicted_preferred)}, "
            f"по факту {_window_text(observed_preferred)}"
        )
    remarks += [NOTE_RULE, NOTE_NO_EFFECT]
    return Verification(
        line=line,
        predicted=predicted,
        observed=observed,
        observed_availability=observed_availability,
        observed_preferred=observed_preferred,
        observed_insufficient=observed_insufficient,
        lead_time_minutes=lead,
        first_observed_alarm=first_alarm,
        first_predicted_warning=first_warning,
        first_predicted_alarm=first_predicted_alarm,
        alarm_lead_time_minutes=alarm_lead,
        hit_minutes=counts.hit,
        miss_minutes=counts.miss,
        false_alarm_minutes=counts.false_alarm,
        quiet_minutes=counts.quiet,
        unverified_minutes=counts.unverified,
        predicted_unknown_minutes=counts.predicted_unknown,
        window_choice_stable=stable,
        note="; ".join(remarks),
    )


def observe(req: AssessmentRequest, store: RawStore) -> Observed | None:
    """Факт по тем же правилам окон, что расчёт: ограничения запроса и освещённость по траектории факта.

    Окна-кандидаты и запрошенное окно факта оцениваются `windows.compare.score` с `constraints` запроса и линией
    `illumination`, как в `api.service.run_assessment`; без ограничений освещённость окно не исключает (D43).
    Окна, начинающиеся раньше отсечения, исключаются и в факте: сравнивается тот же набор окон, что был доступен
    прогнозу (D80).
    """
    asof = AsOf(store, None)
    span = Window(start=req.search_start, end=req.search_end)
    found = oem_for(asof, span)
    if found is None:
        return None
    eph, rec = found
    trajectory = from_oem(eph, provenance_of(rec), span, is_reconstruction=True)
    points: list[Any] = []
    sep = SepLine(load_sep_config()).assess(span, trajectory, asof, points_out=points)
    meteors = MeteorLine(load_meteor_config()).assess(span, trajectory, asof)
    illumination = IlluminationLine().assess(span, trajectory, asof)
    rules = load_window_rules()
    duration = timedelta(hours=req.duration_hours)
    lines = [sep, meteors]
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
    return Observed(
        sep=sep,
        exposed=exposed_times(points),
        meteors=meteors,
        windows=scores,
        recommendation=recommend(scores, rules, requested=requested),
    )


def exposure_counts(
    predicted: frozenset[datetime], observed: frozenset[datetime], span: Window, step_minutes: float = 1.0
) -> tuple[float, float, float]:
    """Попадания, пропуски и ложные тревоги по минутам экспозиции внутри периода: пересечение и разности множеств."""
    p = {t for t in predicted if span.start <= t < span.end}
    o = {t for t in observed if span.start <= t < span.end}
    return len(p & o) * step_minutes, len(o - p) * step_minutes, len(p - o) * step_minutes


def exposure_in(observed: frozenset[datetime], window: Window | None, step_minutes: float = 1.0) -> float | None:
    """Минуты экспозиции по факту внутри окна; без окна числа нет."""
    if window is None:
        return None
    return sum(1 for t in observed if window.start <= t < window.end) * step_minutes


def _alarm_minutes(intervals: tuple[Interval, ...], span: Window) -> float:
    total = 0.0
    for iv in intervals:
        lo, hi = max(iv.start, span.start), min(iv.end, span.end)
        if hi > lo and iv.level in ALARM_LEVELS:
            total += (hi - lo).total_seconds() / 60.0
    return total


def _baseline_check(b: Baseline, req: AssessmentRequest, truth: Observed, span: Window) -> BaselineCheck:
    cutoff = req.cutoff
    if cutoff is None:
        raise ValueError("сверка бейзлайна требует cutoff режима replay")
    v = compare(
        cutoff,
        b.line.intervals,
        truth.sep.intervals,
        span,
        predicted_preferred=b.recommendation.preferred,
        observed_preferred=truth.recommendation.preferred,
        observed_insufficient=truth.recommendation.insufficient,
        observed_availability=truth.sep.availability,
        observed_equivalents=truth.recommendation.equivalents,
        notes=(f"бейзлайн «{b.title}»: {b.basis}",),
    )
    hit: float | None = None
    miss: float | None = None
    false_alarm: float | None = None
    in_preferred: float | None = None
    if truth.sep.availability == "available":
        observed = {t for t in truth.exposed if span.start <= t < span.end}
        hit = float(sum(1 for t in observed if level_at(b.line.intervals, t) in ALARM_LEVELS))
        miss = len(observed) - hit
        false_alarm = _alarm_minutes(b.line.intervals, span) - hit
        in_preferred = exposure_in(truth.exposed, b.recommendation.preferred)
    return BaselineCheck(
        name=b.name,
        title=b.title,
        rule=b.line.intervals[0].rule if b.line.intervals else "",
        level=b.level,
        basis=b.basis,
        provenance=b.provenance,
        preferred=b.recommendation.preferred,
        insufficient=b.recommendation.insufficient,
        preferred_exposure_minutes=in_preferred,
        exposure_hit_minutes=hit,
        exposure_miss_minutes=miss,
        exposure_false_alarm_minutes=false_alarm,
        lead_time_minutes=v.lead_time_minutes,
        window_choice_stable=v.window_choice_stable,
        note=v.note,
    )


def baseline_checks(
    result: AssessmentResult, store: RawStore, truth: Observed
) -> tuple[tuple[BaselineCheck, ...], tuple[str, ...]]:
    """Три бейзлайна на отсечении расчёта и их сверка; отказ бейзлайна не отменяет расчёт, причина идёт в заметку."""
    req = result.request
    span = Window(start=req.search_start, end=req.search_end)
    others = tuple(
        a for a in result.lines if a.line in BASELINE_OTHER_LINES and a.availability != "unusable_for_replay"
    )
    illumination = next((a for a in result.lines if a.line == ILLUMINATION_LINE), None)
    checks: list[BaselineCheck] = []
    failures: list[str] = []
    for name, build in (("persistence", persistence), ("single_alert", single_alert), ("swpc_outlook", swpc_outlook)):
        try:
            baseline = build(req, store, other_lines=others, illumination=illumination)
            checks.append(_baseline_check(baseline, req, truth, span))
        except Exception as exc:
            failures.append(NOTE_BASELINE_FAILED.format(name=name, reason=f"{type(exc).__name__}: {exc}"))
    return tuple(checks), tuple(failures)


def verify(
    result: AssessmentResult, store: RawStore, predicted_exposed: frozenset[datetime] | None = None
) -> Verification:
    req = result.request
    if req.cutoff is None:
        raise ValueError("сверка с фактом требует cutoff режима replay")
    span = Window(start=req.search_start, end=req.search_end)
    predicted = next((a.intervals for a in result.lines if a.line == SEP_LINE), ())
    truth = observe(req, store)
    if truth is None:
        return compare(
            req.cutoff,
            predicted,
            (),
            span,
            predicted_preferred=result.recommendation.preferred,
            observed_preferred=None,
            observed_insufficient=True,
            observed_availability="missing",
            notes=(NOTE_NO_TRAJECTORY,),
        )
    verification = compare(
        req.cutoff,
        predicted,
        truth.sep.intervals,
        span,
        predicted_preferred=result.recommendation.preferred,
        observed_preferred=truth.recommendation.preferred,
        observed_insufficient=truth.recommendation.insufficient,
        observed_availability=truth.sep.availability,
        observed_equivalents=truth.recommendation.equivalents,
    )
    checks, failures = baseline_checks(result, store, truth)
    notes = [verification.note, *failures]
    if checks:
        notes.append(NOTE_BASELINES)
    update: dict[str, Any] = {"baselines": checks}
    if truth.sep.availability == "available":
        update["requested_exposure_minutes"] = exposure_in(truth.exposed, result.requested_window.window)
        update["preferred_exposure_minutes"] = exposure_in(truth.exposed, result.recommendation.preferred)
        if predicted_exposed is not None:
            hit, miss, false_alarm = exposure_counts(predicted_exposed, truth.exposed, span)
            update |= {
                "exposure_hit_minutes": hit,
                "exposure_miss_minutes": miss,
                "exposure_false_alarm_minutes": false_alarm,
            }
            notes.append(NOTE_EXPOSURE.format(hit=hit, miss=miss, false_alarm=false_alarm))
    return verification.model_copy(update={**update, "note": "; ".join(notes)})
