import tomllib
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from evarisk.types import Frozen, Interval, Level, LineAssessment, Recommendation, Window, WindowScore
from evarisk.windows.explain import explain
from evarisk.windows.measures import (
    BEFORE_CUTOFF_REASON,
    CRITICAL_REASON,
    GAP_REASON,
    ILLUMINATION_NO_DATA,
    ILLUMINATION_PREFIX,
    ILLUMINATION_SHADOW_START,
    MISSING_REASON,
    elevated_hours,
    exposure_hours,
    unknown_hours,
)

LEVELS: tuple[Level, ...] = ("none", "elevated", "high", "critical", "unknown")
# Точность сравнения штрафов при ранжировании: 1e-9 ч, много меньше минуты и шага сетки.
PENALTY_DIGITS = 9
# Числа причины это суммы минут по линиям штрафа: линии идут одновременно, сумма бывает больше окна (D84).
SUM_DANGER = "линия-минут high+critical (сумма по линиям)"
SUM_ELEVATED = "линия-минут elevated (сумма по линиям)"
SUM_UNKNOWN = "линия-минут без уровня (сумма по линиям)"


class WindowRules(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    step_minutes: int = 15
    weight_high: float = 1.0
    weight_elevated: float = 0.25
    equivalence_tolerance_hours: float = 0.25
    critical_excludes: bool = True


class Constraints(Frozen):
    """Явно заданные пользователем условия работ; в штраф окон не входят, только исключают окно (D43)."""

    min_sunlit_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    start_in_daylight: bool = False

    @property
    def any_set(self) -> bool:
        return self.min_sunlit_fraction is not None or self.start_in_daylight


def constraint_rules(constraints: Constraints | None) -> tuple[str, ...]:
    """Строки для `rules` ответа: по одной на каждое заданное ограничение."""
    if constraints is None:
        return ()
    rules: list[str] = []
    if constraints.min_sunlit_fraction is not None:
        rules.append(
            f"{ILLUMINATION_PREFIX}: доля света в окне не ниже {constraints.min_sunlit_fraction:.2f} "
            f"(задано пользователем, в штрафе не участвует)"
        )
    if constraints.start_in_daylight:
        rules.append(f"{ILLUMINATION_PREFIX}: старт на свету (задано пользователем, в штрафе не участвует)")
    return tuple(rules)


def sunlit_fraction(window: Window, illumination: LineAssessment) -> float | None:
    """Доля света в окне по интервалам линии освещённости; `None`, если интервалы окно не покрывают."""
    hours = overlap_hours(window, illumination.intervals)
    covered = hours["none"] + hours["elevated"]
    if covered <= 0.0:
        return None
    return hours["none"] / covered


def starts_in_daylight(window: Window, illumination: LineAssessment) -> bool | None:
    """Освещён ли старт окна; `None`, если у линии нет интервала на момент старта."""
    for iv in illumination.intervals:
        if iv.start <= window.start < iv.end:
            return iv.level == "none"
    return None


def illumination_reasons(
    window: Window, constraints: Constraints | None, illumination: LineAssessment | None
) -> list[str]:
    if constraints is None or not constraints.any_set:
        return []
    if illumination is None or illumination.availability != "available":
        return [ILLUMINATION_NO_DATA]
    reasons: list[str] = []
    if constraints.min_sunlit_fraction is not None:
        fraction = sunlit_fraction(window, illumination)
        if fraction is None:
            reasons.append(ILLUMINATION_NO_DATA)
        elif fraction < constraints.min_sunlit_fraction:
            reasons.append(f"{ILLUMINATION_PREFIX}: доля света {fraction:.2f} < {constraints.min_sunlit_fraction:.2f}")
    if constraints.start_in_daylight:
        lit = starts_in_daylight(window, illumination)
        if lit is None:
            reasons.append(ILLUMINATION_NO_DATA)
        elif not lit:
            reasons.append(ILLUMINATION_SHADOW_START)
    return list(dict.fromkeys(reasons))


def load_window_rules(path: Path = Path("config/windows.toml")) -> WindowRules:
    with path.open("rb") as fh:
        return WindowRules(**tomllib.load(fh))


def candidates(search_start: datetime, search_end: datetime, duration: timedelta, rules: WindowRules) -> list[Window]:
    result: list[Window] = []
    start = search_start
    step = timedelta(minutes=rules.step_minutes)
    while start + duration <= search_end:
        result.append(Window(start=start, end=start + duration))
        start += step
    return result


def overlap_hours(window: Window, intervals: Iterable[Interval]) -> dict[Level, float]:
    hours: dict[Level, float] = {level: 0.0 for level in LEVELS}
    for iv in intervals:
        lo, hi = max(window.start, iv.start), min(window.end, iv.end)
        if hi > lo:
            hours[iv.level] += (hi - lo).total_seconds() / 3600.0
    return hours


def score(
    window: Window,
    assessments: list[LineAssessment],
    rules: WindowRules,
    constraints: Constraints | None = None,
    illumination: LineAssessment | None = None,
    cutoff: datetime | None = None,
) -> WindowScore:
    """Часы по уровням для каждой линии штрафа и причины исключения.

    Линия освещённости в `per_line` не входит: тень это условие работ, а не опасность, и окно исключается
    только по явно заданным `constraints` (D43). `cutoff` это момент отсечения replay: окно, начинающееся раньше
    него, на момент прогноза уже в прошлом и исключается (D80).
    """
    per_line: dict[str, dict[Level, float]] = {}
    reasons: list[str] = []
    if cutoff is not None and window.start < cutoff:
        reasons.append(BEFORE_CUTOFF_REASON)
    duration_hours = (window.end - window.start).total_seconds() / 3600.0
    unknown_limit_hours = rules.step_minutes / 60.0
    known_share = 0.0
    for a in assessments:
        hours = overlap_hours(window, a.intervals)
        per_line[a.line] = hours
        if a.availability == "available":
            known = sum(hours.values()) - hours["unknown"]
            known_share += max(0.0, min(1.0, known / duration_hours))
            # Пропуск данных не равен нулевому риску: окно с пробелом длиннее шага сетки не сравнивается (D70).
            if round(hours["unknown"] - unknown_limit_hours, PENALTY_DIGITS) > 0:
                reasons.append(f"пробел данных по линии {a.line}: {hours['unknown'] * 60:.0f} мин без уровня (D70)")
        else:
            reasons.append(f"недостаточно данных по линии {a.line} ({a.availability})")
        if rules.critical_excludes and hours["critical"] > 0:
            reasons.append(f"critical по линии {a.line}: {hours['critical']:.2f} ч")
    reasons.extend(illumination_reasons(window, constraints, illumination))
    # Полнота это доля минут окна с известным уровнем, усреднённая по линиям штрафа: линия без данных даёт 0,
    # минуты unknown внутри доступной линии полноту уменьшают.
    completeness = known_share / len(assessments) if assessments else 0.0
    return WindowScore(
        window=window,
        per_line=per_line,
        completeness=completeness,
        excluded_reason="; ".join(reasons) if reasons else None,
    )


def penalty(s: WindowScore, rules: WindowRules) -> float:
    total = 0.0
    for hours in s.per_line.values():
        total += rules.weight_high * (hours["high"] + hours["critical"]) + rules.weight_elevated * hours["elevated"]
    return total


def summarize_exclusions(scores: list[WindowScore]) -> str:
    """Причины исключения всех окон, сгруппированные по линии и виду, вместо перечисления по каждому окну."""
    total = len(scores)
    critical: dict[str, list[float]] = {}
    gaps: dict[str, list[float]] = {}
    missing: dict[tuple[str, str], int] = {}
    other: dict[str, int] = {}
    for s in scores:
        for part in (s.excluded_reason or "").split("; "):
            if not part:
                continue
            if m := CRITICAL_REASON.match(part):
                critical.setdefault(m.group(1), []).append(float(m.group(2)))
            elif m := GAP_REASON.match(part):
                gaps.setdefault(m.group(1), []).append(float(m.group(2)))
            elif m := MISSING_REASON.match(part):
                key = (m.group(1), m.group(2))
                missing[key] = missing.get(key, 0) + 1
            else:
                other[part] = other.get(part, 0) + 1
    parts: list[str] = []
    for line, hours in critical.items():
        span = f"{min(hours):.2f} ч" if min(hours) == max(hours) else f"{min(hours):.2f}–{max(hours):.2f} ч"
        parts.append(f"critical по линии {line} в {len(hours)} окнах из {total} ({span})")
    for line, minutes in gaps.items():
        low, high = min(minutes), max(minutes)
        span = f"{low:.0f} мин" if low == high else f"{low:.0f}–{high:.0f} мин"
        parts.append(f"пробел данных по линии {line} в {len(minutes)} окнах из {total} ({span} без уровня, D70)")
    for (line, availability), n in missing.items():
        parts.append(f"нет данных по линии {line} ({availability}) в {n} окнах из {total}")
    for text, n in other.items():
        parts.append(f"{text} в {n} окнах из {total}")
    return f"все {total} окон исключены: " + "; ".join(parts)


def _minutes(s: WindowScore) -> float:
    return exposure_hours(s) * 60.0


def equivalent(s: WindowScore, best: WindowScore, rules: WindowRules) -> bool:
    """Равнозначность (D79, уточняет D48): столько же целых минут high+critical, разница штрафа не больше допуска
    и разница часов без уровня не больше одного шага сетки (D56). Допуск в шаг сетки по минутам high+critical
    оставлял запрошенное окно с 7 минутами high при соседнем окне без них.
    """
    step_hours = rules.step_minutes / 60.0
    return (
        penalty(s, rules) - penalty(best, rules) <= rules.equivalence_tolerance_hours
        and round(_minutes(s)) <= round(_minutes(best))
        and round(unknown_hours(s) - unknown_hours(best), PENALTY_DIGITS) <= step_hours
    )


def recommend(scores: list[WindowScore], rules: WindowRules, requested: WindowScore | None = None) -> Recommendation:
    """Выбор окна с техническим `reason` для аудита и объяснением обычными словами для человека (D76)."""
    rec = _choose(scores, rules, requested)
    return rec.model_copy(update={"explanation": explain(scores, rec, requested)})


def _choose(scores: list[WindowScore], rules: WindowRules, requested: WindowScore | None) -> Recommendation:
    step_hours = rules.step_minutes / 60.0
    applied = (
        f"шаг сетки {rules.step_minutes} мин",
        f"штраф = weight_high {rules.weight_high} × часы(high+critical) "
        f"+ weight_elevated {rules.weight_elevated} × часы(elevated)",
        "окно с critical исключается" if rules.critical_excludes else "окно с critical не исключается",
        "линия без данных исключает окно из рекомендации (D28)",
        f"окно, где по линии штрафа больше {rules.step_minutes} мин без уровня, исключается: пропуск данных не равен "
        "нулевому риску (D70)",
        f"окна равнозначны при равных целых минутах high+critical, разнице штрафа до "
        f"{rules.equivalence_tolerance_hours} ч и разнице часов без уровня до {step_hours:g} ч, один шаг сетки "
        "(D79, D56)",
        "запрошенное окно сохраняется, если оно равнозначно лучшим: перенос без выигрыша не предлагается (D45)",
        "перенос предлагается на окно с минимумом минут high+critical; среди таких окон со штрафом в пределах "
        "допуска выбирается ближайшее к запрошенному (D49)",
    )
    eligible = [s for s in scores if s.excluded_reason is None]
    if not eligible:
        return Recommendation(
            preferred=None,
            reason=f"оснований для выбора нет: {summarize_exclusions(scores)}",
            equivalents=(),
            insufficient=True,
            rules_applied=applied,
        )
    # Часы по интервалам суммируются в float: разница порядка 1e-15 ч это шум, а не меньший штраф.
    # Порядок: минуты high+critical (то, по чему действует аналитик), минуты без уровня, штраф, начало окна.
    ranked = sorted(
        eligible,
        key=lambda s: (
            round(exposure_hours(s), PENALTY_DIGITS),
            round(unknown_hours(s), PENALTY_DIGITS),
            round(penalty(s, rules), PENALTY_DIGITS),
            s.window.start,
        ),
    )
    best = ranked[0]
    within = [s for s in ranked if equivalent(s, best, rules)]
    homogeneous = len(within) == len(eligible)
    if requested is not None and requested.excluded_reason is None and equivalent(requested, best, rules):
        others = tuple(s.window for s in within if s.window != requested.window)
        reason = (
            f"перенос не улучшает: запрошенное окно равнозначно лучшим ({SUM_DANGER} "
            f"{_minutes(requested):.0f} при минимуме {_minutes(best):.0f}, штраф {penalty(requested, rules):.2f} ч "
            f"при {penalty(best, rules):.2f} ч у лучшего; D45, D79)"
        )
        if homogeneous and len(eligible) > 1:
            reason = f"{reason}; обстановка на периоде поиска однородна: ни одно допустимое окно не лучше запрошенного"
        return Recommendation(
            preferred=requested.window, reason=reason, equivalents=others, insufficient=False, rules_applied=applied
        )
    # Перенос предлагается на окно с минимумом минут high+critical; среди таких окон со штрафом в пределах допуска
    # берётся ближайшее к запрошенному: минимальное изменение плана (D49).
    clean = [
        s
        for s in within
        if round(exposure_hours(s) - exposure_hours(best), PENALTY_DIGITS) <= 0
        and round(unknown_hours(s) - unknown_hours(best), PENALTY_DIGITS) <= 0
    ]
    if requested is not None:
        anchor = requested.window.start
        chosen = min(clean, key=lambda s: (abs((s.window.start - anchor).total_seconds()), s.window.start))
    else:
        chosen = best
    equivalents = tuple(s.window for s in within if s.window != chosen.window)
    tail = (
        f"; равнозначными по D79 (те же минуты high+critical, штраф в пределах допуска) считаются "
        f"ещё {len(equivalents)}"
        if equivalents
        else ""
    )
    if requested is not None and requested.excluded_reason is None:
        unknown_part = (
            f", {SUM_UNKNOWN} {unknown_hours(requested) * 60:.0f} против {unknown_hours(chosen) * 60:.0f}"
            if unknown_hours(requested) > 0 or unknown_hours(chosen) > 0
            else ""
        )
        # Минуты elevated названы всегда: при равных минутах high+critical перенос решает именно их вклад в штраф.
        elevated_part = (
            f", {SUM_ELEVATED} {elevated_hours(requested) * 60:.0f} против {elevated_hours(chosen) * 60:.0f}"
        )
        reason = (
            f"запрошенное окно хуже: {SUM_DANGER} {_minutes(requested):.0f} против {_minutes(chosen):.0f}"
            f"{elevated_part}{unknown_part}, штраф {penalty(requested, rules):.2f} ч против "
            f"{penalty(chosen, rules):.2f} ч; выбрано ближайшее к запрошенному из {len(clean)} окон с минимумом таких "
            f"минут (D49){tail}"
        )
    elif requested is not None:
        reason = (
            f"запрошенное окно исключено; выбрано ближайшее к нему из {len(clean)} допустимых окон с минимумом "
            f"{SUM_DANGER} {_minutes(chosen):.0f}, штраф {penalty(chosen, rules):.2f} ч (D49){tail}"
        )
    elif equivalents:
        reason = (
            f"минимум {SUM_DANGER} {_minutes(chosen):.0f}, штраф {penalty(chosen, rules):.2f} ч{tail}, "
            "при равенстве выбрано самое раннее"
        )
    elif len(ranked) > 1:
        reason = (
            f"минимум {SUM_DANGER} {_minutes(chosen):.0f}, штраф {penalty(chosen, rules):.2f} ч против "
            f"{penalty(ranked[1], rules):.2f} ч у следующего"
        )
    else:
        reason = f"единственное допустимое окно, штраф {penalty(chosen, rules):.2f} ч"
    return Recommendation(
        preferred=chosen.window, reason=reason, equivalents=equivalents, insufficient=False, rules_applied=applied
    )
