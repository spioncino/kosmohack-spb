import re
from collections import Counter
from datetime import datetime

from evarisk.api.schemas import AssessmentResult
from evarisk.api.service import LIMIT_DISABLED_SOURCES
from evarisk.briefing.models import (
    BriefingFacts,
    DataGap,
    Exclusion,
    ExclusionKind,
    ExposedInterval,
    FactWindow,
    Question,
    Verdict,
)
from evarisk.risk.lines.sep import LIMIT_MARGINAL_PASSES
from evarisk.types import Level, LineAssessment, Provenance, Window, WindowScore
from evarisk.windows.explain import duration_text
from evarisk.windows.measures import (
    CRITICAL_REASON,
    GAP_REASON,
    ILLUMINATION_NO_DATA,
    ILLUMINATION_SHADOW_START,
    MISSING_REASON,
    SUNLIT_REASON,
    unmeasured_flux_minutes,
)

SEP_LINE = "sep_protons"
EXPOSED_SHOWN = 6
PROVENANCE_SHOWN = 8
DANGEROUS: tuple[Level, ...] = ("high", "critical")
BASIS_REPLAY = "прогноз из прошлого: по данным на {moment} UTC, позже опубликованное не использовано"
BASIS_REVIEW = "разбор по архиву, не прогноз: использованы все опубликованные данные"
BASIS_CURRENT = "расчёт сейчас: данные на {moment} UTC"
_DISABLED_PREFIX = LIMIT_DISABLED_SOURCES.split("{")[0].rstrip()
_MARGINAL_PREFIX = LIMIT_MARGINAL_PASSES.split("{")[0].rstrip()
_FREE_TEXT_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_FREE_TEXT_TIME = re.compile(r"\b\d{1,2}:\d{2}\b")


def num(value: float) -> str:
    """Число в том виде, в каком оно попадает и в список разрешённых значений, и в шаблонный текст."""
    return f"{value:g}"


def hm(moment: datetime) -> str:
    return f"{moment:%H:%M}"


def ymd(moment: datetime) -> str:
    return f"{moment:%Y-%m-%d}"


def _minutes(hours: float) -> float:
    return float(round(hours * 60.0))


def duration(minutes: float) -> str:
    """Длительность словами для слуха: «4 ч 42 мин», «6 ч», «45 мин»; тот же вид, что в объяснении рекомендации."""
    return duration_text(round(minutes))


def shift_minutes(requested: FactWindow, recommended: FactWindow | None) -> float | None:
    """Сдвиг начала нового окна относительно запрошенного в минутах, без знака; без нового окна `None`."""
    if recommended is None or recommended.start == requested.start:
        return None
    return float(round(abs((recommended.start - requested.start).total_seconds()) / 60.0))


def _window_minutes(score: WindowScore, levels: tuple[Level, ...]) -> float | None:
    """Сумма минут по линиям штрафа (линия-минуты): для сравнения окон, в текст не идёт (D84)."""
    if not score.per_line:
        return None
    return _minutes(sum(sum(hours[level] for level in levels) for hours in score.per_line.values()))


def _line_minutes(score: WindowScore, levels: tuple[Level, ...]) -> dict[str, float]:
    """Минуты уровней по каждой линии штрафа: каждое число не больше длины окна (D84)."""
    return {line: _minutes(sum(hours[level] for level in levels)) for line, hours in score.per_line.items()}


def line_values(window: FactWindow | None) -> list[float]:
    """Все минуты окна по линиям: их и только их текст вправе назвать; суммы по линиям сюда не входят (D84)."""
    if window is None:
        return []
    return [*window.exposure_by_line.values(), *window.elevated_by_line.values(), *window.unknown_by_line.values()]


def fact_window(score: WindowScore, sep: LineAssessment | None = None) -> FactWindow:
    window = score.window
    flux = None if sep is None or sep.availability != "available" else unmeasured_flux_minutes(window, sep.intervals)
    return FactWindow(
        flux_unmeasured_minutes=None if flux is None else float(flux),
        start=window.start,
        end=window.end,
        duration_hours=(window.end - window.start).total_seconds() / 3600.0,
        exposure_minutes=_window_minutes(score, DANGEROUS),
        unknown_minutes=_window_minutes(score, ("unknown",)),
        elevated_minutes=_window_minutes(score, ("elevated",)),
        elevated_lines=tuple(line for line, hours in score.per_line.items() if _minutes(hours["elevated"]) > 0),
        exposure_by_line=_line_minutes(score, DANGEROUS),
        unknown_by_line=_line_minutes(score, ("unknown",)),
        elevated_by_line=_line_minutes(score, ("elevated",)),
    )


def _verdict(result: AssessmentResult) -> Verdict:
    rec = result.recommendation
    if rec.insufficient or rec.preferred is None:
        return "insufficient"
    return "keep_requested" if rec.preferred == result.requested_window.window else "move_to"


def _score_of(result: AssessmentResult, window: Window) -> WindowScore | None:
    if result.requested_window.window == window:
        return result.requested_window
    for score in result.windows:
        if score.window == window:
            return score
    return None


def _basis(result: AssessmentResult) -> str:
    req = result.request
    if req.mode == "replay" and req.cutoff is not None:
        return BASIS_REPLAY.format(moment=f"{ymd(req.cutoff)} {hm(req.cutoff)}")
    if req.mode == "review":
        return BASIS_REVIEW
    moment = result.asof or result.created_at
    return BASIS_CURRENT.format(moment=f"{ymd(moment)} {hm(moment)}")


def _disabled_sources(result: AssessmentResult) -> tuple[str, ...]:
    for limitation in result.limitations:
        if limitation.startswith(_DISABLED_PREFIX):
            tail = limitation[len(_DISABLED_PREFIX) :].split(";")[0]
            return tuple(name.strip() for name in tail.split(",") if name.strip())
    return ()


def _marginal_note(result: AssessmentResult) -> str | None:
    for limitation in result.limitations:
        if limitation.startswith(_MARGINAL_PREFIX):
            return limitation
    return None


def _exposed(
    result: AssessmentResult, scoring: tuple[str, ...]
) -> tuple[tuple[ExposedInterval, ...], datetime | None, list[Provenance]]:
    """Отрезки high и critical внутри запрошенного окна, момент их конца в периоде и их провенанс.

    Момент конца это `None`, когда такие минуты тянутся до конца периода поиска: обещать чистое время
    после периода нельзя.
    """
    requested = result.requested_window.window
    span_start, span_end = result.request.search_start, result.request.search_end
    items: list[ExposedInterval] = []
    sources: list[Provenance] = []
    last: datetime | None = None
    for assessment in result.lines:
        if assessment.line not in scoring:
            continue
        for interval in assessment.intervals:
            if interval.level not in DANGEROUS:
                continue
            lo, hi = max(interval.start, requested.start), min(interval.end, requested.end)
            if hi > lo:
                items.append(
                    ExposedInterval(
                        start=lo,
                        end=hi,
                        line=assessment.line,
                        level=interval.level,
                        minutes=float(round((hi - lo).total_seconds() / 60.0)),
                    )
                )
                sources.extend(interval.provenance)
            if interval.end > span_start and interval.start < span_end:
                end = min(interval.end, span_end)
                last = end if last is None or end > last else last
    items.sort(key=lambda item: (item.start, item.line))
    return tuple(items), (last if last is not None and last < span_end else None), sources


def _provenance(
    result: AssessmentResult, scoring: tuple[str, ...], exposed: list[Provenance]
) -> tuple[Provenance, ...]:
    by_id: dict[str, Provenance] = {}
    for provenance in exposed:
        by_id.setdefault(provenance.raw_id, provenance)
    for assessment in result.lines:
        if assessment.line in scoring and assessment.intervals:
            for provenance in assessment.intervals[0].provenance:
                by_id.setdefault(provenance.raw_id, provenance)
    by_id.setdefault(result.trajectory.provenance.raw_id, result.trajectory.provenance)
    return tuple(list(by_id.values())[:PROVENANCE_SHOWN])


def _text_tokens(texts: tuple[str | None, ...]) -> list[str]:
    """Числа и времена свободных строк фактов: модель вправе процитировать их, остальное это выдумка."""
    tokens: list[str] = []
    for text in texts:
        if not text:
            continue
        tokens.extend(_FREE_TEXT_TIME.findall(text))
        tokens.extend(_FREE_TEXT_NUMBER.findall(_FREE_TEXT_TIME.sub(" ", text)))
    return tokens


def _allowed_values(
    moments: tuple[datetime | None, ...],
    numbers: tuple[float | None, ...],
    texts: tuple[str | None, ...],
    durations: tuple[float | None, ...] = (),
) -> tuple[str, ...]:
    """Разрешённые значения текста; длительность от часа и больше идёт ещё и в виде «4 ч 42 мин» (D78).

    Длительности посчитаны здесь, а не моделью: перевести 282 мин в часы модель не вправе.
    """
    values: list[str] = []
    for moment in moments:
        if moment is not None:
            values.append(hm(moment))
            values.append(ymd(moment))
    for value in numbers:
        if value is not None:
            values.append(num(value))
    values.extend(_text_tokens(texts))
    values.extend(duration(value) for value in durations if value is not None and round(value) >= 60)
    return tuple(dict.fromkeys(values))


def _exclusion_kinds(reason: str | None) -> list[tuple[ExclusionKind, str | None]]:
    """Виды причин исключения окна по тексту `excluded_reason` из `windows.compare.score`."""
    found: list[tuple[ExclusionKind, str | None]] = []
    for part in (reason or "").split("; "):
        if not part:
            continue
        if m := CRITICAL_REASON.match(part):
            found.append(("critical", m.group(1)))
        elif m := GAP_REASON.match(part):
            found.append(("data_gap", m.group(1)))
        elif m := MISSING_REASON.match(part):
            found.append(("no_data", m.group(1)))
        elif SUNLIT_REASON.match(part):
            found.append(("light", None))
        elif part == ILLUMINATION_SHADOW_START:
            found.append(("shadow_start", None))
        elif part == ILLUMINATION_NO_DATA:
            found.append(("no_light_data", None))
        else:
            found.append(("other", None))
    return list(dict.fromkeys(found))


def _exclusions(scores: list[WindowScore]) -> tuple[Exclusion, ...]:
    counts = Counter(kind for s in scores for kind in _exclusion_kinds(s.excluded_reason))
    return tuple(Exclusion(kind=kind, line=line, windows=n) for (kind, line), n in counts.most_common())


def build_facts(result: AssessmentResult, question: Question) -> BriefingFacts:
    """Факты для сводки: вердикт, окна, экспозиция, пробелы данных и провенанс из сохранённого расчёта.

    Модель эту функцию не вызывает и на неё не влияет: числа берутся из структурных полей результата,
    текст рекомендации не разбирается.
    """
    scoring = tuple(result.requested_window.per_line)
    verdict = _verdict(result)
    sep = next((a for a in result.lines if a.line == SEP_LINE), None)
    requested = fact_window(result.requested_window, sep)
    preferred = result.recommendation.preferred
    recommended_score = None if preferred is None else _score_of(result, preferred)
    recommended = None if recommended_score is None else fact_window(recommended_score, sep)
    exposed, clear_after, exposed_sources = _exposed(result, scoring)
    # Экипажу называются пробелы только по линиям, которые участвуют в сравнении окон: информационная линия дозы
    # и линия без архива источника на выбор окна не влияют и остаются в ограничениях результата.
    gaps = tuple(
        DataGap(line=line, availability=availability)
        for line, availability in result.data_availability.items()
        if availability != "available" and line in scoring
    )
    marginal = _marginal_note(result)
    depends_on = None if sep is None else sep.confidence.depends_on
    excluded = sum(1 for score in result.windows if score.excluded_reason is not None)
    exclusions = _exclusions(list(result.windows))
    shift = shift_minutes(requested, recommended if verdict == "move_to" else None)
    unmeasured = None if sep is None or sep.availability != "available" else sep.metrics.get("unmeasured_minutes")
    search_minutes = _minutes((result.request.search_end - result.request.search_start).total_seconds() / 3600.0)
    # Минуты окон по линиям, а не их суммы (D84): «12 ч» повышенного фона в окне 6 ч назвать нельзя.
    minutes_of_windows = (*line_values(requested), *line_values(recommended))
    return BriefingFacts(
        assessment_id=result.id,
        question=question,
        mode=result.request.mode,
        data_moment=result.asof,
        cutoff=result.request.cutoff,
        basis=_basis(result),
        verdict=verdict,
        requested=requested,
        recommended=recommended,
        exposed=exposed[:EXPOSED_SHOWN],
        exposed_total=len(exposed),
        clear_after=clear_after,
        gaps=gaps,
        disabled_sources=_disabled_sources(result),
        confidence_grade=None if sep is None else sep.confidence.grade,
        confidence_depends_on=depends_on,
        marginal_note=marginal,
        windows_total=len(result.windows),
        windows_excluded=excluded,
        provenance=_provenance(result, scoring, exposed_sources),
        algorithm_version=result.algorithm_version,
        exclusions=exclusions,
        requested_exclusions=_exclusions([result.requested_window]),
        unmeasured_minutes=unmeasured,
        search_minutes=search_minutes,
        allowed_values=_allowed_values(
            moments=(
                requested.start,
                requested.end,
                None if recommended is None else recommended.start,
                None if recommended is None else recommended.end,
                clear_after,
                result.asof,
                result.request.cutoff,
                *(item.start for item in exposed[:EXPOSED_SHOWN]),
                *(item.end for item in exposed[:EXPOSED_SHOWN]),
            ),
            numbers=(
                requested.duration_hours,
                None if recommended is None else recommended.duration_hours,
                *minutes_of_windows,
                float(len(exposed)),
                float(len(gaps)),
                float(len(result.windows)),
                float(excluded),
                *(item.minutes for item in exposed[:EXPOSED_SHOWN]),
                *(float(item.windows) for item in exclusions),
                shift,
                unmeasured,
            ),
            texts=(depends_on, marginal),
            durations=(
                *minutes_of_windows,
                requested.duration_hours * 60.0,
                None if recommended is None else recommended.duration_hours * 60.0,
                *(item.minutes for item in exposed[:EXPOSED_SHOWN]),
                shift,
                unmeasured,
            ),
        ),
    )
