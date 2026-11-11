"""Факты для своего вопроса экипажа (D89): сводка плюс то, о чём спрашивают после неё.

Всё собирается из сохранённого `AssessmentResult` без модели: объяснение выбора окна, окна рядом с временами из
вопроса и в пределах часа от запрошенного и рекомендованного начала, тень, опасные отрезки, надёжность оценок.
Каждое число этих строк добавляется в `allowed_values`, других чисел у модели нет.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise

from evarisk.api.schemas import AssessmentResult
from evarisk.briefing.facts import DANGEROUS, SEP_LINE, build_facts, duration, fact_window, hm
from evarisk.briefing.models import BriefingFacts
from evarisk.briefing.templates import DANGER_SPOKEN, LINE_NAMES, by_line, line_dative
from evarisk.briefing.validate import value_tokens
from evarisk.types import Window, WindowScore
from evarisk.windows.explain import explain, plain_reason

ILLUMINATION_LINE = "illumination"
GRID = timedelta(minutes=15)
AROUND = timedelta(hours=1)
WINDOWS_SHOWN = 8
SEGMENTS_SHOWN = 6
SHADOWS_SHOWN = 6
GRADE_SPOKEN = {"low": "низкая", "medium": "средняя", "high": "высокая"}
_CLOCK = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
# «на час позже», «на 30 минут раньше», «на полчаса», «на 2 часа», «задержимся на час».
_SHIFT = re.compile(
    r"(?<![а-яё])на\s+(?:(\d+(?:[.,]\d+)?)\s*)?(полчаса|полтора\s+часа|час[а-яё]*|ч|минут[а-яё]*|мин)(?![а-яё])",
    re.IGNORECASE,
)
_EARLIER = re.compile(r"раньше|пораньше|ранее", re.IGNORECASE)
_LATER = re.compile(r"позже|попозже|позднее|задерж|опозда|отлож", re.IGNORECASE)
# Подпись окна по времени, названному в вопросе; модель по ней отвечает на «а если в 08:00?».
NAMED = "в вопросе названо"


@dataclass(frozen=True)
class Target:
    """Время начала, о котором спрашивают: подпись для модели и момент на сетке окон."""

    label: str
    start: datetime


@dataclass(frozen=True)
class AskContext:
    """Факты вопроса: факты сводки с расширенным `allowed_values` и строки, которые добавляются к листу фактов."""

    facts: BriefingFacts
    lines: tuple[str, ...]


def _shift_amount(number: str | None, unit: str) -> timedelta:
    unit = unit.lower()
    if unit == "полчаса":
        return timedelta(minutes=30)
    if unit.startswith("полтора"):
        return timedelta(minutes=90)
    value = 1.0 if number is None else float(number.replace(",", "."))
    return timedelta(minutes=value * (1.0 if unit.startswith("мин") else 60.0))


def shifts(asked: str) -> list[timedelta]:
    """Сдвиги начала из вопроса со знаком: «на час позже» это +1 ч, «на 30 минут раньше» это −30 мин.

    Без слова направления рядом со сдвигом направление берётся из всего вопроса («задержимся»), а если его нет,
    сдвиг даётся в обе стороны.
    """
    found: list[timedelta] = []
    for match in _SHIFT.finditer(asked):
        amount = _shift_amount(match.group(1), match.group(2))
        if amount <= timedelta(0):
            continue
        near = asked[match.end() : match.end() + 20]
        if _EARLIER.search(near):
            found.append(-amount)
        elif _LATER.search(near) or _LATER.search(asked):
            found.append(amount)
        elif _EARLIER.search(asked):
            found.append(-amount)
        else:
            found.extend([amount, -amount])
    return list(dict.fromkeys(found))


def _on_day(result: AssessmentResult, hour: int, minute: int) -> datetime:
    """Момент с этим временем суток: внутри периода поиска и ближе всего к запрошенному началу."""
    req = result.request
    anchor = result.requested_window.window.start
    base = anchor.replace(hour=hour, minute=minute, second=0, microsecond=0)
    options = [base + timedelta(days=d) for d in (-1, 0, 1, 2)]
    inside = [t for t in options if req.search_start <= t <= req.search_end]
    return min(inside or options, key=lambda t: abs(t - anchor))


def step(result: AssessmentResult) -> timedelta:
    """Шаг сетки начал окон по самим окнам расчёта; без соседних окон `GRID`."""
    starts = sorted({score.window.start for score in result.windows})
    return min((b - a for a, b in pairwise(starts)), default=GRID)


def _snap(result: AssessmentResult, moment: datetime) -> datetime:
    origin, grid = result.request.search_start, step(result)
    return origin + round((moment - origin) / grid) * grid


def targets(result: AssessmentResult, asked: str) -> list[Target]:
    """Начала окон, о которых вопрос: времена ЧЧ:ММ, сдвиги от запрошенного и рекомендованного, всегда ±1 ч."""
    requested = result.requested_window.window.start
    preferred = result.recommendation.preferred
    starts = [("запрошенного", requested)]
    if preferred is not None and preferred.start != requested:
        starts.append(("рекомендованного", preferred.start))
    lowered = asked.lower()
    if "запрошен" in lowered:
        asked_bases = starts[:1]
    elif preferred is not None and ("рекомендова" in lowered or "нов" in lowered):
        asked_bases = starts[-1:]
    else:
        asked_bases = starts
    found: list[Target] = []
    for hour, minute in _CLOCK.findall(asked):
        found.append(Target(f"{NAMED} {int(hour):02d}:{minute}", _on_day(result, int(hour), int(minute))))
    for shift in shifts(asked):
        for name, start in asked_bases:
            side = "позже" if shift > timedelta(0) else "раньше"
            text = duration(abs(shift).total_seconds() / 60.0)
            found.append(Target(f"по вопросу, на {text} {side} {name}", start + shift))
    for name, start in starts:
        for shift in (-AROUND, AROUND):
            side = "позже" if shift > timedelta(0) else "раньше"
            found.append(Target(f"на 1 ч {side} {name}", start + shift))
    return found


def _score_at(result: AssessmentResult, start: datetime) -> WindowScore | None:
    for score in (result.requested_window, *result.windows):
        if score.window.start == start:
            return score
    return None


def _nearest(result: AssessmentResult, start: datetime) -> WindowScore | None:
    scores = (result.requested_window, *result.windows)
    return min(scores, key=lambda score: abs(score.window.start - start), default=None)


def _clip(start: datetime, end: datetime, window: Window) -> tuple[datetime, datetime] | None:
    lo, hi = max(start, window.start), min(end, window.end)
    return (lo, hi) if hi > lo else None


def _spans(items: list[tuple[datetime, datetime]], shown: int) -> str:
    text = ", ".join(f"с {hm(lo)} до {hm(hi)}" for lo, hi in items[:shown])
    rest = len(items) - shown
    return text + (f" и ещё {rest}" if rest > 0 else "")


def window_line(result: AssessmentResult, label: str, score: WindowScore) -> str:
    """Окно-кандидат одной строкой: границы, опасные минуты и повышенный фон по линиям, исключено ли и почему."""
    fw = fact_window(score)
    window = score.window
    parts = [f"{label}: окно с {hm(window.start)} до {hm(window.end)} UTC"]
    parts.append(f"опасных минут {by_line(fw.exposure_by_line) or 'нет'}")
    parts.append(f"повышенного фона {by_line(fw.elevated_by_line, duration) or 'нет'}")
    unknown = by_line(fw.unknown_by_line, duration)
    if unknown:
        parts.append(f"без данных {unknown}")
    if score.excluded_reason:
        parts.append(f"окно исключено, причина: {plain_reason(score.excluded_reason)}")
    else:
        parts.append("окно не исключено")
    if window in result.recommendation.equivalents:
        parts.append("почти равноценно выбранному")
    return ", ".join(parts) + "."


def candidate_lines(result: AssessmentResult, asked: str) -> list[str]:
    """Строки окон рядом с временами вопроса и в пределах часа от запрошенного и рекомендованного начала."""
    requested = result.requested_window.window
    preferred = result.recommendation.preferred
    seen: set[datetime] = set()
    lines: list[str] = []
    for target in targets(result, asked):
        start = _snap(result, target.start)
        if start in seen or len(seen) >= WINDOWS_SHOWN:
            continue
        seen.add(start)
        if start == requested.start:
            lines.append(f"{target.label}: это начало запрошенного окна.")
            continue
        if preferred is not None and start == preferred.start:
            lines.append(f"{target.label}: это начало рекомендованного окна.")
            continue
        score = _score_at(result, start)
        if score is not None:
            # Окно есть в расчёте: это сказано словами, иначе модель отвечала «окна с 08:00 нет» при строке о нём.
            named = target.label.startswith(NAMED)
            lines.append(
                window_line(result, f"{target.label}, такое окно в расчёте есть" if named else target.label, score)
            )
            continue
        lines.append(f"{target.label}: окна с началом в {hm(start)} UTC в периоде поиска нет.")
        nearest = _nearest(result, start)
        if nearest is not None and nearest.window.start not in seen:
            seen.add(nearest.window.start)
            lines.append(window_line(result, f"ближайшее к {hm(start)} окно периода", nearest))
    return lines


def shadow_line(result: AssessmentResult, name: str, window: Window) -> str:
    """Тень внутри окна по линии освещённости: уровень elevated у неё это тень (`risk/lines/illumination.py`)."""
    line = next((a for a in result.lines if a.line == ILLUMINATION_LINE), None)
    if line is None or line.availability != "available":
        return f"Тень в {name} окне: данных об освещённости нет."
    shadows = [c for i in line.intervals if i.level == "elevated" and (c := _clip(i.start, i.end, window))]
    if not shadows:
        return f"Тень в {name} окне: нет, всё окно на свету."
    minutes = sum((hi - lo).total_seconds() for lo, hi in shadows) / 60.0
    start = "окно начинается в тени" if shadows[0][0] == window.start else "окно начинается на свету"
    return (
        f"Тень в {name} окне {_spans(shadows, SHADOWS_SHOWN)}, отрезков тени {len(shadows)}, в тени всего "
        f"{duration(minutes)}, остальное время на свету, {start}."
    )


def danger_line(result: AssessmentResult, name: str, window: Window) -> str:
    """Опасные отрезки окна по видам опасности: «протоны достают станцию с 04:36 до 04:50, …»."""
    scoring = tuple(result.requested_window.per_line)
    groups: list[str] = []
    for assessment in result.lines:
        if assessment.line not in scoring:
            continue
        for level in DANGEROUS:
            spans = [
                c for i in assessment.intervals if i.level == level and (c := _clip(i.start, i.end, window)) is not None
            ]
            if spans:
                what = DANGER_SPOKEN.get((assessment.line, level), f"опасный уровень, {LINE_NAMES[assessment.line]}")
                groups.append(f"{what} {_spans(spans, SEGMENTS_SHOWN)}")
    return f"Опасные отрезки в {name} окне: " + ("; ".join(groups) if groups else "нет") + "."


def confidence_line(result: AssessmentResult) -> str:
    """Надёжность оценки по каждому виду опасности словами, без кодов и коэффициентов."""
    scoring = tuple(result.requested_window.per_line)
    items = [
        f"по {line_dative(a.line)} {GRADE_SPOKEN[a.confidence.grade]}"
        for a in result.lines
        if a.line in scoring and a.line != SEP_LINE
    ]
    return ("Надёжность оценки " + ", ".join(items) + ".") if items else ""


def explanation(result: AssessmentResult) -> tuple[str, ...]:
    """Объяснение выбора окна обычными словами: сохранённое, а у расчётов до D76 собранное из тех же оценок окон."""
    rec = result.recommendation
    return rec.explanation or explain(list(result.windows), rec, result.requested_window)


def ask_context(result: AssessmentResult, asked: str) -> AskContext:
    """Факты сводки и строки для вопроса; числа строк добавлены в `allowed_values`."""
    facts = build_facts(result, "summary")
    requested = result.requested_window.window
    preferred = result.recommendation.preferred
    windows = [("запрошенном", requested)]
    if preferred is not None and preferred != requested:
        windows.append(("рекомендованном", preferred))
    req = result.request
    lines = [
        f"Период поиска с {hm(req.search_start)} до {hm(req.search_end)} UTC, начала окон через "
        f"{duration(step(result).total_seconds() / 60.0)}.",
        f"Почему выбрано это окно, объяснение расчёта: {' '.join(explanation(result))}",
    ]
    lines.extend(danger_line(result, name, window) for name, window in windows)
    lines.extend(shadow_line(result, name, window) for name, window in windows)
    confidence = confidence_line(result)
    if confidence:
        lines.append(confidence)
    lines.append("Окна рядом, о которых могут спросить:")
    lines.extend(candidate_lines(result, asked))
    tokens = [token for line in lines for token in value_tokens(line)]
    allowed = tuple(dict.fromkeys([*facts.allowed_values, *tokens]))
    return AskContext(facts=facts.model_copy(update={"allowed_values": allowed}), lines=tuple(lines))
