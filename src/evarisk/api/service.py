import json
import math
import re
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from evarisk.api.schemas import (
    AccessGeometry,
    AssessmentRequest,
    AssessmentResult,
    ConjunctionRow,
    ConjunctionsLatest,
    ManualEntry,
    OemInfo,
    OrbitLatest,
    RulesSnapshot,
    SaaPass,
    TrajectoryQuery,
    TrajectoryResponse,
    TrajectorySample,
    TrajectorySummary,
)
from evarisk.config import Settings
from evarisk.orbit.oem import parse_oem
from evarisk.orbit.trajectory import Trajectory, from_oem, load_trajectory_config, saa_passes
from evarisk.parsers.socrates import parse_socrates
from evarisk.replay.verify import verify
from evarisk.risk.config import load_sep_config
from evarisk.risk.lines.access_geometry import AccessGeometryLine
from evarisk.risk.lines.conjunctions import (
    LIMIT_MAXPROB,
    ConjunctionLine,
    conjunction_level,
    is_coorbiting,
    load_conjunction_config,
)
from evarisk.risk.lines.dose_proxy import DoseProxyLine, load_dose_proxy_config
from evarisk.risk.lines.illumination import IlluminationLine
from evarisk.risk.lines.meteors import MeteorLine, load_meteor_config
from evarisk.risk.lines.sep import SepLine, exposed_times
from evarisk.store.asof import AsOf
from evarisk.store.backfill import covered_days, ranges_text
from evarisk.store.raw import RawStore
from evarisk.store.views import oem_for, provenance_of
from evarisk.timeutil import utc_now
from evarisk.types import Level, LineAssessment, Provenance, Recommendation, Window
from evarisk.version import algorithm_version
from evarisk.windows.compare import (
    candidates,
    constraint_rules,
    load_window_rules,
    overlap_hours,
    recommend,
    score,
)
from evarisk.windows.explain import (
    LINE_GENITIVE,
    before_tail,
    cap,
    day_text,
    duration_text,
    forecast_zero,
    joined,
    line_name,
    plural,
)
from evarisk.windows.measures import unmeasured_flux_minutes, unmeasured_state

LIMIT_REVIEW = (
    "разбор обстановки по архивным сведениям: реконструкция по всем опубликованным данным, не прогноз; "
    "геометрия это реконструкция"
)
_SAMPLE_EVERY = 2
ProgressCallback = Callable[[str, float], None]
LIMIT_CONJUNCTIONS_HISTORICAL = (
    "сближения: архива SOCRATES нет, в исторических режимах линия показывается, но в сравнении окон не участвует (D30)"
)
LIMIT_DOSE_INFORMATIONAL = "прокси дозы среды информационная: в сравнении окон не участвует (D34)"
LIMIT_DISABLED_SOURCES = (
    "источники отключены пользователем и в расчёте не участвуют: {names}; линии, которые от них зависят, считаются "
    "по оставшимся данным (внешний прогноз, сообщения), минуты без данных показываются без уровня, линия без данных "
    "исключает окна; отключение не превращается в благоприятную обстановку"
)
LIMIT_OUTSIDE_ARCHIVE = (
    "период поиска вне покрытия архива потока протонов: суточные файлы goes_sgps в хранилище есть за {ranges}; "
    "измерений на запрошенный период нет, поэтому линия протонов без данных и рекомендации нет"
)
LIMIT_CURRENT_PAST_WINDOW = (
    "режим «сейчас» для окна в прошлом: расчёт по всем данным на момент запроса, как разбор; отчёт SOCRATES "
    "покрывает только текущую неделю, поэтому для прошлого окна подходит режим «разбор»"
)
LIMIT_TRAJECTORY_NO_LINES = (
    "линии риска на траектории не рассчитаны, level у всех точек unknown; уровни даёт POST /assessments или POST /jobs"
)
NO_TRAJECTORY_MESSAGE = "нет эфемериды МКС, доступной на момент запроса и покрывающей период с запасом в час"
GP_SOURCE, GP_PRODUCT = "celestrak", "gp"
OEM_SOURCE, OEM_PRODUCT = "nasa_oem", "oem"
GOES_SOURCE = "goes_sgps"

PROGRESS_STEPS: dict[str, float] = {
    "archive": 0.1,
    "trajectory": 0.2,
    "line:sep_protons": 0.5,
    "line:meteor_showers": 0.7,
    "line:conjunctions": 0.75,
    "line:dose_proxy": 0.8,
    "line:illumination": 0.85,
    "windows": 0.9,
    "saved": 1.0,
}


class NoTrajectoryData(Exception):  # noqa: N818
    pass


def _asof_time(req: AssessmentRequest) -> datetime | None:
    if req.mode == "review":
        return None
    if req.mode == "current":
        return utc_now()
    return req.cutoff


def _level_at(assessment: LineAssessment, moment: datetime) -> Level:
    for iv in assessment.intervals:
        if iv.start <= moment < iv.end:
            return iv.level
    return "unknown"


def _sources(lines: list[LineAssessment], trajectory_provenance: Provenance) -> list[Provenance]:
    by_id = {p.raw_id: p for a in lines for iv in a.intervals for p in iv.provenance}
    by_id.setdefault(trajectory_provenance.raw_id, trajectory_provenance)
    return list(by_id.values())


def _report(on_progress: ProgressCallback | None, step: str) -> None:
    if on_progress is not None:
        on_progress(step, PROGRESS_STEPS[step])


def _sample(trajectory: Trajectory, i: int, level: Level) -> TrajectorySample:
    mlat = float(trajectory.mlat_deg[i])
    return TrajectorySample(
        t=trajectory.times[i],
        lat_deg=float(trajectory.lat_deg[i]),
        lon_deg=float(trajectory.lon_deg[i]),
        alt_km=float(trajectory.alt_km[i]),
        sunlit=bool(trajectory.sunlit[i]),
        mlat_deg=None if math.isnan(mlat) else mlat,
        in_saa=bool(trajectory.in_saa[i]),
        level=level,
    )


RULE_SCORING_LINES = (
    "в сравнении окон участвуют линии: {lines}; информационная линия dose_proxy (D34) и условие работ illumination "
    "без заданных ограничений (D43) окно не исключают и в штраф не входят"
)
_EXPOSURE_SHOWN = 6


_Exposure = tuple[datetime, datetime, Level, str]


def exposure_spans(
    requested: Window, search: Window, lines: list[LineAssessment]
) -> tuple[list[_Exposure], datetime | None]:
    """Отрезки high и critical линий штрафа внутри запрошенного окна и конец последнего такого отрезка в периоде."""
    inside: list[_Exposure] = []
    last: datetime | None = None
    for a in lines:
        for iv in a.intervals:
            if iv.level not in ("high", "critical"):
                continue
            lo, hi = max(iv.start, requested.start), min(iv.end, requested.end)
            if hi > lo:
                inside.append((lo, hi, iv.level, a.line))
            if iv.end > search.start and iv.start < search.end:
                end = min(iv.end, search.end)
                last = end if last is None or end > last else last
    return sorted(inside), last


def _exposure_note(inside: list[_Exposure], last: datetime | None, search: Window) -> str:
    """Минуты high и critical линий штрафа внутри запрошенного окна и момент, после которого их в периоде нет."""
    if not inside or last is None:
        return ""
    shown = ", ".join(f"{lo:%H:%M}–{hi:%H:%M} {line} {level}" for lo, hi, level, line in inside[:_EXPOSURE_SHOWN])
    more = f" и ещё {len(inside) - _EXPOSURE_SHOWN}" if len(inside) > _EXPOSURE_SHOWN else ""
    tail = f"; после {last:%Y-%m-%d %H:%M} UTC таких минут в периоде поиска нет" if last < search.end else ""
    return f"минуты high и critical в запрошенном окне по линиям: {shown}{more}{tail}"


LEVEL_PLAIN = {"high": "высокий", "critical": "критический"}


def exposure_plain(inside: list[_Exposure], last: datetime | None, search: Window) -> str:
    """То же обычными словами для объяснения рекомендации (D76, D78).

    Подряд идущие отрезки одной линии и уровня идут одной группой: «критический уровень протонов 04:36–04:50,
    05:24–05:33 и ещё 1 отрезок».
    """
    if not inside or last is None:
        return ""
    groups: list[tuple[str, list[str]]] = []
    for lo, hi, level, line in inside[:_EXPOSURE_SHOWN]:
        what = f"{LEVEL_PLAIN.get(level, level)} уровень {LINE_GENITIVE.get(line, line_name(line))}"
        if not groups or groups[-1][0] != what:
            groups.append((what, []))
        groups[-1][1].append(f"{lo:%H:%M}–{hi:%H:%M}")
    shown = "; ".join(f"{what} {', '.join(spans)}" for what, spans in groups)
    rest = len(inside) - _EXPOSURE_SHOWN
    more = f" и ещё {rest} {plural(rest, 'отрезок', 'отрезка', 'отрезков')}" if rest > 0 else ""
    tail = (
        f" После {last:%H:%M} UTC {day_text(last)} опасных отрезков до конца периода поиска нет."
        if last < search.end
        else ""
    )
    return f"Опасные отрезки в запрошенном окне: {shown}{more}.{tail}"


SAA_RULE = "ЮАА по трассе: модуль поля IGRF (ppigrf) на высоте орбиты ниже {threshold:.0f} нТл; {source}"
SAA_NOTE = "минимум |B| {min_b:.0f} нТл в {min_at:%Y-%m-%d %H:%M} UTC; положение станции по эфемериде {provider}"


def _saa_passes(trajectory: Trajectory) -> list[SaaPass]:
    cfg = load_trajectory_config()
    if cfg.saa_b_threshold_nt is None:
        return []
    rule = SAA_RULE.format(threshold=cfg.saa_b_threshold_nt, source=cfg.sources["saa"])
    return [
        SaaPass(
            start=p.start,
            end=p.end,
            min_b_nt=p.min_b_nt,
            min_at=p.min_at,
            threshold_nt=cfg.saa_b_threshold_nt,
            rule=rule,
            note=SAA_NOTE.format(min_b=p.min_b_nt, min_at=p.min_at, provider=trajectory.provider),
            provenance=[trajectory.provenance],
        )
        for p in saa_passes(trajectory)
    ]


UNMEASURED_SHARE = 0.5
NOTE_UNMEASURED = (
    "поток протонов не измерен на {minutes:g} мин из {total:g} мин периода поиска: уровень на них взят из внешнего "
    "прогноза и сообщений о событиях, это не измеренное спокойствие (D64)"
)
PLAIN_UNMEASURED = (
    "Поток протонов не измерен на {minutes} из {total} периода поиска. Там уровень взят из прогноза и сообщений "
    "о событиях."
)
# Линия без архива на момент данных в сравнении окон не участвует (D30, D65); объяснение называет её и причину.
UNSCORED_WHY = {
    "conjunctions": "архива отчётов о сближениях за то время нет",
    "meteor_showers": "годовой прогноз метеоров вышел позже момента данных",
}
UNSCORED_OTHER = "архивных данных за то время нет"


def unmeasured_plain(minutes: float, total: float) -> str:
    return PLAIN_UNMEASURED.format(minutes=duration_text(round(minutes)), total=duration_text(round(total)))


def unscored_plain(dropped: list[LineAssessment]) -> str:
    """«Сближения в сравнении окон не участвуют: архива отчётов о сближениях за то время нет.»; без линий пусто."""
    lines = [a.line for a in dropped]
    if not lines:
        return ""
    why = list(dict.fromkeys(UNSCORED_WHY.get(line, UNSCORED_OTHER) for line in lines))
    return f"{cap(joined([line_name(line) for line in lines]))} в сравнении окон не участвуют: {joined(why)}."


def with_notes(
    rec: Recommendation,
    requested: Window,
    span: Window,
    sep: LineAssessment,
    compared: list[LineAssessment],
    dropped: list[LineAssessment],
) -> Recommendation:
    """Заметки к рекомендации: технические в `reason`, те же сведения обычными словами в `explanation`.

    Опасные отрезки запрошенного окна и линии вне сравнения окон идут перед концом объяснения, неизмеренный поток
    протонов второй фразой (D64, D76, D78). Ноль опасных минут в окне с неизмеренным потоком получает оговорку
    «но это по прогнозу» (D84).
    """
    reason, explanation = rec.reason, rec.explanation
    if sep.availability == "available":
        chosen = rec.preferred
        explanation = forecast_zero(
            explanation,
            unmeasured_state(unmeasured_flux_minutes(requested, sep.intervals), requested),
            "" if chosen is None else unmeasured_state(unmeasured_flux_minutes(chosen, sep.intervals), chosen),
        )
    inside, last = exposure_spans(requested, span, compared)
    exposure_note = _exposure_note(inside, last, span)
    if exposure_note and not rec.insufficient:
        reason = f"{reason}; {exposure_note}"
        explanation = before_tail(explanation, rec, exposure_plain(inside, last, span))
    unmeasured = _unmeasured(sep, span)
    if unmeasured is not None and not rec.insufficient and explanation:
        reason = f"{NOTE_UNMEASURED.format(minutes=unmeasured[0], total=unmeasured[1])}; {reason}"
        head, *rest = explanation
        explanation = (head, unmeasured_plain(*unmeasured), *rest)
    explanation = before_tail(explanation, rec, unscored_plain(dropped))
    return rec.model_copy(update={"reason": reason, "explanation": explanation})


def _unmeasured(sep: LineAssessment, span: Window) -> tuple[float, float] | None:
    minutes = sep.metrics.get("unmeasured_minutes")
    total = (span.end - span.start).total_seconds() / 60.0
    if sep.availability != "available" or minutes is None or total <= 0 or minutes / total <= UNMEASURED_SHARE:
        return None
    return minutes, total


GEOMETRY_CLEAR = (
    "страховка по геометрии (D61, информационно): в запрошенном окне нет минут, на которых трасса была бы открыта "
    "протонам даже при Kp {kp:g}"
)
GEOMETRY_SAFER = (
    "страховка по геометрии (D61, информационно): при протонном событии и Kp {kp:g} в запрошенном окне трасса открыта "
    "{requested:g} мин; среди предпочтительного и равнозначных окон меньше всего таких минут у окна "
    "{start:%H:%M}–{end:%H:%M} UTC: {safest:g} мин; рекомендация от этого не меняется"
)
GEOMETRY_NO_BETTER = (
    "страховка по геометрии (D61, информационно): при протонном событии и Kp {kp:g} в запрошенном окне трасса открыта "
    "{requested:g} мин; среди предпочтительного и равнозначных окон меньшего числа таких минут нет"
)


def _open_minutes(window: Window, access: LineAssessment) -> float:
    return round(overlap_hours(window, access.intervals)["elevated"] * 60.0, 1)


def access_geometry(access: LineAssessment, requested: Window, rec: Recommendation) -> AccessGeometry | None:
    """Минуты возможного доступа в запрошенном и предпочтительном окнах и самое устойчивое из допустимых окон.

    Кандидаты это предпочтительное окно и равнозначные ему: только среди них перенос не ухудшает оценку по
    действующим правилам. При равенстве минут берётся окно, ближайшее к запрошенному по началу.
    """
    if access.availability != "available":
        return None
    kp = access.metrics["kp"]
    requested_minutes = _open_minutes(requested, access)
    preferred_minutes = None if rec.preferred is None else _open_minutes(rec.preferred, access)
    pool = [w for w in (rec.preferred, *rec.equivalents) if w is not None]
    safest: Window | None = None
    safest_minutes: float | None = None
    if pool and not rec.insufficient:
        safest = min(pool, key=lambda w: (_open_minutes(w, access), abs((w.start - requested.start).total_seconds())))
        safest_minutes = _open_minutes(safest, access)
    if requested_minutes == 0.0:
        note = GEOMETRY_CLEAR.format(kp=kp)
    elif safest is not None and safest_minutes is not None and safest_minutes < requested_minutes:
        note = GEOMETRY_SAFER.format(
            kp=kp, requested=requested_minutes, start=safest.start, end=safest.end, safest=safest_minutes
        )
    else:
        note = GEOMETRY_NO_BETTER.format(kp=kp, requested=requested_minutes)
    return AccessGeometry(
        kp=kp,
        requested_minutes=requested_minutes,
        preferred_minutes=preferred_minutes,
        safest=safest,
        safest_minutes=safest_minutes,
        note=note,
    )


def outside_archive_note(store: RawStore, span: Window) -> str | None:
    """Текст ограничения, если ни одни сутки периода поиска не покрыты суточными файлами goes_sgps.

    Архив может состоять из нескольких кусков: промежуток между ними покрытием не считается.
    """
    covered = covered_days(store)
    if not covered:
        return None
    last_day = (span.end - timedelta(microseconds=1)).date()
    day = span.start.date()
    while day <= last_day:
        if day in covered:
            return None
        day += timedelta(days=1)
    return LIMIT_OUTSIDE_ARCHIVE.format(ranges=ranges_text(list(covered)))


def _age_hours(t: datetime | None, epoch: datetime) -> float | None:
    return None if t is None else (t - epoch).total_seconds() / 3600.0


def run_assessment(
    req: AssessmentRequest,
    store: RawStore,
    settings: Settings,
    on_progress: ProgressCallback | None = None,
    disabled: frozenset[str] = frozenset(),
    extra_limitations: tuple[str, ...] = (),
) -> AssessmentResult:
    """Расчёт по запросу; `disabled` это источники в состоянии disabled, их записи из хранилища не читаются.

    `extra_limitations` это строки вызывающей стороны для ограничений результата, например отчёт о докачке архива.
    """
    if disabled:
        store = store.without(disabled)
    t = _asof_time(req)
    asof = AsOf(store, t)
    duration = timedelta(hours=req.duration_hours)
    span = Window(start=req.search_start, end=req.search_end)
    found = oem_for(asof, span)
    if found is None:
        raise NoTrajectoryData(NO_TRAJECTORY_MESSAGE)
    eph, oem_rec = found
    trajectory = from_oem(eph, provenance_of(oem_rec), span, is_reconstruction=(req.mode == "review"))
    _report(on_progress, "trajectory")
    rules = load_window_rules()
    sep_cfg = load_sep_config()
    line = SepLine(sep_cfg)
    sep_points: list[Any] = []
    assessment = line.assess(span, trajectory, asof, points_out=sep_points)
    _report(on_progress, "line:sep_protons")
    meteors = MeteorLine(load_meteor_config()).assess(span, trajectory, asof)
    _report(on_progress, "line:meteor_showers")
    conjunction_asof = AsOf(store, None) if req.mode == "current" else AsOf(store, req.cutoff or req.search_start)
    conjunction_now = utc_now() if req.mode == "current" else None
    conjunctions = ConjunctionLine(load_conjunction_config(), now=conjunction_now).assess(
        span, trajectory, conjunction_asof
    )
    _report(on_progress, "line:conjunctions")
    dose = DoseProxyLine(load_dose_proxy_config(), sep_cfg).assess(span, trajectory, asof)
    _report(on_progress, "line:dose_proxy")
    illumination = IlluminationLine().assess(span, trajectory, asof)
    _report(on_progress, "line:illumination")
    access = AccessGeometryLine(sep_cfg).assess(span, trajectory, asof)
    lines = [assessment, meteors, conjunctions, dose, illumination, access]
    # Линия без архива на момент отсечения (сближения, метеоры до выхода годового прогноза) вне сравнения окон.
    scoring_lines = [a for a in (assessment, meteors, conjunctions) if a.availability != "unusable_for_replay"]
    scores = [
        score(w, scoring_lines, rules, constraints=req.constraints, illumination=illumination, cutoff=req.cutoff)
        for w in candidates(req.search_start, req.search_end, duration, rules)
    ]
    requested = score(
        Window(start=req.eva_start, end=req.eva_start + duration),
        scoring_lines,
        rules,
        constraints=req.constraints,
        illumination=illumination,
    )
    rec = recommend(scores, rules, requested=requested)
    dropped = [a for a in (assessment, meteors, conjunctions) if a.availability == "unusable_for_replay"]
    rec = with_notes(rec, requested.window, span, assessment, scoring_lines, dropped)
    _report(on_progress, "windows")
    limitations = list(dict.fromkeys(lim for a in lines for lim in a.limitations))
    if conjunctions.availability == "unusable_for_replay":
        limitations.append(LIMIT_CONJUNCTIONS_HISTORICAL)
    limitations.append(LIMIT_DOSE_INFORMATIONAL)
    if req.mode == "review":
        limitations.append(LIMIT_REVIEW)
    if req.mode == "current" and t is not None and req.search_end < t:
        limitations.append(LIMIT_CURRENT_PAST_WINDOW)
    if disabled:
        limitations.append(LIMIT_DISABLED_SOURCES.format(names=", ".join(sorted(disabled))))
    if req.mode != "current" and assessment.availability == "missing":
        outside = outside_archive_note(store, span)
        if outside is not None:
            limitations.append(outside)
    limitations.extend(extra_limitations)
    samples = [
        _sample(trajectory, i, _level_at(assessment, trajectory.times[i]))
        for i in range(0, len(trajectory.times), _SAMPLE_EVERY)
    ]
    age = _age_hours(t, trajectory.epoch)
    result = AssessmentResult(
        id=uuid.uuid4().hex[:12],
        created_at=utc_now(),
        request=req,
        asof=t,
        algorithm_version=algorithm_version(),
        trajectory=TrajectorySummary(
            provider=trajectory.provider,
            epoch=trajectory.epoch,
            is_reconstruction=trajectory.is_reconstruction,
            provenance=trajectory.provenance,
            age_at_query_hours=age,
            samples=samples,
            saa_passes=_saa_passes(trajectory),
        ),
        lines=lines,
        requested_window=requested,
        windows=scores,
        recommendation=rec,
        sources_used=_sources(lines, trajectory.provenance),
        data_availability={a.line: a.availability for a in lines},
        limitations=limitations,
        rules=[
            *rec.rules_applied,
            RULE_SCORING_LINES.format(lines=", ".join(a.line for a in scoring_lines)),
            *constraint_rules(req.constraints),
        ],
    )
    result = result.model_copy(update={"geometry": access_geometry(access, requested.window, rec)})
    if req.mode == "replay":
        result = result.model_copy(update={"verification": verify(result, store, exposed_times(sep_points))})
    settings.assessments_root.mkdir(parents=True, exist_ok=True)
    (settings.assessments_root / f"{result.id}.json").write_text(result.model_dump_json(indent=1), encoding="utf-8")
    _report(on_progress, "saved")
    return result


ASSESSMENT_ID = re.compile(r"^[0-9a-f]{12}$")


def load_assessment(assessment_id: str, settings: Settings) -> AssessmentResult | None:
    """Сохранённый расчёт по идентификатору; `None`, когда такого расчёта нет.

    Идентификатор это 12 шестнадцатеричных знаков (`uuid4().hex[:12]`): имя файла сводки
    `<id>.briefing.<вопрос>.<язык>` и любой другой путь под этот вид не подходят и расчётом не считаются.
    Файл, который не разбирается как `AssessmentResult`, тоже означает отсутствие расчёта: ответ расчёта это
    либо разобранный результат, либо «не найден».
    """
    if ASSESSMENT_ID.match(assessment_id) is None:
        return None
    path = settings.assessments_root / f"{assessment_id}.json"
    if not path.exists():
        return None
    try:
        return AssessmentResult.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def iso_z(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def list_assessments(settings: Settings) -> list[dict[str, str]]:
    """Список сохранённых расчётов.

    Рядом с расчётом лежат файлы сводки `<id>.briefing.<question>.<language>.json`; расчёт это файл, имя
    которого состоит из одного идентификатора, поэтому имена с точкой в основе пропускаются.
    """
    results: list[AssessmentResult] = []
    if settings.assessments_root.exists():
        for path in sorted(settings.assessments_root.glob("*.json")):
            if "." in path.stem or ASSESSMENT_ID.match(path.stem) is None:
                continue
            try:
                results.append(AssessmentResult.model_validate_json(path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                # Файл, который не разбирается как расчёт, в список не входит: по идентификатору он тоже даёт 404.
                continue
    results.sort(key=lambda r: (r.created_at, r.id), reverse=True)
    return [
        {
            "id": r.id,
            "created_at": iso_z(r.created_at),
            "mode": r.request.mode,
            "eva_start": iso_z(r.request.eva_start),
        }
        for r in results
    ]


def build_trajectory(
    query: TrajectoryQuery, store: RawStore, disabled: frozenset[str] = frozenset()
) -> TrajectoryResponse:
    """Траектория по OEM тем же путём, что в run_assessment, но без линий и без запаса по краям.

    Момент данных `t` это `None` для `review` и `utc_now()` для `current`; сетка от `start` до `end` включительно
    с шагом `step_minutes`, каждая точка сетки попадает в `samples`, `level` у всех точек `unknown`.
    """
    if disabled:
        store = store.without(disabled)
    t = None if query.mode == "review" else utc_now()
    span = Window(start=query.start, end=query.end)
    found = oem_for(AsOf(store, t), span)
    if found is None:
        raise NoTrajectoryData(NO_TRAJECTORY_MESSAGE)
    eph, oem_rec = found
    trajectory = from_oem(
        eph,
        provenance_of(oem_rec),
        span,
        step=timedelta(minutes=query.step_minutes),
        pad=timedelta(0),
        is_reconstruction=(query.mode == "review"),
    )
    limitations = [*trajectory.limitations, LIMIT_TRAJECTORY_NO_LINES]
    if query.mode == "review":
        limitations.append(LIMIT_REVIEW)
    return TrajectoryResponse(
        provider=trajectory.provider,
        epoch=trajectory.epoch,
        is_reconstruction=trajectory.is_reconstruction,
        provenance=trajectory.provenance,
        age_at_query_hours=_age_hours(t, trajectory.epoch),
        samples=[_sample(trajectory, i, "unknown") for i in range(len(trajectory.times))],
        saa_passes=_saa_passes(trajectory),
        limitations=limitations,
    )


def _omm(content: bytes) -> dict[str, Any]:
    """Первый объект OMM из ответа CelesTrak `gp.php?FORMAT=JSON` (массив объектов, по одному на спутник)."""
    payload = json.loads(content)
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise ValueError("запись celestrak/gp не содержит массива объектов OMM")
    first: dict[str, Any] = payload[0]
    return first


def latest_orbit(store: RawStore) -> OrbitLatest:
    """Последняя по доступности запись celestrak/gp как OMM и заголовок последнего OEM; без записи `None`."""
    asof = AsOf(store, None)
    gp = asof.latest(GP_SOURCE, GP_PRODUCT)
    oem_rec = asof.latest(OEM_SOURCE, OEM_PRODUCT)
    oem = None
    if oem_rec is not None:
        header = parse_oem(asof.read(oem_rec).decode("utf-8")).header
        oem = OemInfo(
            creation_date=header.creation_date,
            start_time=header.start_time,
            stop_time=header.stop_time,
            provenance=provenance_of(oem_rec),
        )
    return OrbitLatest(
        omm=None if gp is None else _omm(asof.read(gp)),
        provenance=None if gp is None else provenance_of(gp),
        oem=oem,
    )


NOTE_CONJUNCTIONS_CURRENT = (
    "отчёт SOCRATES относится к моменту расчёта и следующим семи суткам; архива нет, к историческим датам он не "
    "применяется (D29, D30)"
)
MANUAL_FILES = ("README.md", "CONTRIBUTING.md")
MANUAL_TEMPLATE = "MODULE_README_TEMPLATE.md"
_TITLE = re.compile(r"^#\s+(.+)$", re.M)


def latest_conjunctions(store: RawStore, now: datetime) -> ConjunctionsLatest:
    """Последний отчёт SOCRATES с уровнями строк по MAXPROB (D53), давностью на `now` и признаком устаревания."""
    cfg = load_conjunction_config()
    asof = AsOf(store, None)
    rec = asof.latest("socrates", "conjunctions_iss")
    limitations = [NOTE_CONJUNCTIONS_CURRENT, LIMIT_MAXPROB]
    if rec is None:
        return ConjunctionsLatest(
            available=False,
            data_as_of=None,
            age_hours=None,
            stale=False,
            max_report_age_hours=cfg.max_report_age_hours,
            interval_start=None,
            interval_stop=None,
            provenance=None,
            conjunctions=[],
            coorbiting_rows=0,
            limitations=["отчёта SOCRATES в хранилище нет", *limitations],
        )
    report = parse_socrates(asof.read(rec).decode("utf-8", errors="replace"))
    age = (now - report.data_as_of).total_seconds() / 3600.0
    stale = age > cfg.max_report_age_hours
    if stale:
        limitations.insert(
            0,
            f"отчёт устарел: давность {age:.1f} ч больше {cfg.max_report_age_hours:g} ч, "
            "текущими сближения не считаются",
        )
    rows = [
        ConjunctionRow(
            tca=c.tca,
            secondary_name=c.secondary_name,
            secondary_catnr=c.secondary_catnr,
            min_range_km=c.min_range_km,
            rel_speed_kms=c.rel_speed_kms,
            max_probability=c.max_probability,
            level=conjunction_level(c, cfg),
            coorbiting=is_coorbiting(c, cfg),
        )
        for c in sorted(report.conjunctions, key=lambda c: c.tca)
    ]
    return ConjunctionsLatest(
        available=True,
        data_as_of=report.data_as_of,
        age_hours=age,
        stale=stale,
        max_report_age_hours=cfg.max_report_age_hours,
        interval_start=report.interval_start,
        interval_stop=report.interval_stop,
        provenance=provenance_of(rec),
        conjunctions=[r for r in rows if not r.coorbiting],
        coorbiting_rows=sum(1 for r in rows if r.coorbiting),
        limitations=limitations,
    )


def manual_path(root: Path, name: str) -> Path | None:
    """Файл документации по имени: `docs/<путь>.md`, README.md или CONTRIBUTING.md корня; выход за каталог это None."""
    if name in MANUAL_FILES:
        candidate = (root / name).resolve()
        return candidate if candidate.is_file() else None
    docs = (root / "docs").resolve()
    candidate = (docs / name).resolve()
    if candidate.suffix != ".md" or not candidate.is_relative_to(docs) or not candidate.is_file():
        return None
    return candidate


def manual_index(root: Path) -> list[ManualEntry]:
    """Документы, которые страница открывает в разделе «Документация»: имя и заголовок первого уровня."""
    names = [n for n in MANUAL_FILES if (root / n).is_file()]
    docs = root / "docs"
    if docs.is_dir():
        # Шаблон README модуля служебный и в список документов для пользователя не входит.
        names += sorted(str(p.relative_to(docs)) for p in docs.glob("*.md") if p.name != MANUAL_TEMPLATE)
    entries: list[ManualEntry] = []
    for name in names:
        path = manual_path(root, name)
        if path is None:
            continue
        m = _TITLE.search(path.read_text(encoding="utf-8"))
        entries.append(ManualEntry(name=name, title=m.group(1).strip() if m else name))
    return entries


def rules_snapshot() -> RulesSnapshot:
    """Правила окон и параметры линии SEP как в конфигах; у правил окон источников нет (наше правило), это `None`."""
    sep = load_sep_config()
    return RulesSnapshot(
        windows=load_window_rules().model_dump(mode="json"),
        sep=sep.model_dump(mode="json", exclude={"sources"}),
        sources={
            "windows": None,
            "sep": sep.sources,
            "meteors": load_meteor_config().sources,
            "conjunctions": load_conjunction_config().sources,
            "dose_proxy": load_dose_proxy_config().sources,
        },
    )
