"""Анализ чувствительности рекомендации к параметрам, у которых нет первоисточника («наше правило»).

Для каждого случая траектория и линия метеоров считаются один раз; затем по одному меняется параметр линии
протонов или правил окон, остальные остаются базовыми, и записывается, изменились ли предпочтительное окно и
минуты high+critical запрошенного окна. Результат: docs/SENSITIVITY.md.

Запуск: uv run python scripts/sensitivity.py
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from evarisk.config import Settings
from evarisk.orbit.trajectory import Trajectory, from_oem
from evarisk.risk.config import SepConfig, load_sep_config
from evarisk.risk.lines.meteors import MeteorLine, load_meteor_config
from evarisk.risk.lines.sep import SepLine
from evarisk.store.asof import AsOf
from evarisk.store.raw import RawStore
from evarisk.store.views import oem_for, provenance_of
from evarisk.types import LineAssessment, Window
from evarisk.windows.compare import WindowRules, candidates, load_window_rules, recommend, score
from evarisk.windows.measures import exposure_hours

DURATION = timedelta(hours=6)


@dataclass(frozen=True)
class Case:
    title: str
    cutoff: datetime | None
    eva_start: datetime
    search: Window


@dataclass(frozen=True)
class Variation:
    parameter: str
    basis: str
    values: tuple[float, ...]


@dataclass(frozen=True)
class Outcome:
    preferred: str
    requested_minutes: float
    preferred_minutes: float
    excluded: int
    total: int


CASES = (
    Case(
        "прогноз из прошлого, отсечение 10 мая 2024 12:30, окно 11 мая 04:00",
        datetime(2024, 5, 10, 12, 30, tzinfo=UTC),
        datetime(2024, 5, 11, 4, 0, tzinfo=UTC),
        Window(start=datetime(2024, 5, 11, 0, 0, tzinfo=UTC), end=datetime(2024, 5, 12, 0, 0, tzinfo=UTC)),
    ),
    Case(
        "разбор по архиву, окно 11 мая 2024 04:00",
        None,
        datetime(2024, 5, 11, 4, 0, tzinfo=UTC),
        Window(start=datetime(2024, 5, 11, 0, 0, tzinfo=UTC), end=datetime(2024, 5, 12, 0, 0, tzinfo=UTC)),
    ),
    Case(
        "разбор по архиву, спокойные сутки, окно 20 июня 2024 06:00",
        None,
        datetime(2024, 6, 20, 6, 0, tzinfo=UTC),
        Window(start=datetime(2024, 6, 20, 0, 0, tzinfo=UTC), end=datetime(2024, 6, 21, 0, 0, tzinfo=UTC)),
    ),
)

SEP_VARIATIONS = (
    Variation(
        "kp_suppression_deg_per_kp", "оценка по рис. 6 Leske 2001, не сверена; база 0.92", (0.7, 0.8, 0.92, 1.0, 1.1)
    ),
    Variation(
        "cutoff_offset_gv",
        "точка отсчёта обрезания (D62): 0.363 ГВ это фит SAMPEX, 63.4°; 0 это диполь без смещения, 69.2°",
        (0.0, 0.2, 0.363, 0.5),
    ),
    Variation(
        "suit_threshold_mev",
        "Leske 2001: 30 MeV через все части скафандра; 10.3 MeV через мягкие части 0.18 г/см² (Wilson, PSTAR)",
        (10.3, 25.0, 30.0, 35.0, 40.0),
    ),
    Variation("p30_event_pfu", "наше правило по аналогии с ESPE; база 1 pfu", (0.5, 1.0, 2.0, 5.0)),
    Variation("proton_outlook_threshold_percent", "наше правило; база 50 %", (30.0, 40.0, 50.0, 55.0, 60.0)),
)
RULE_VARIATIONS = (
    Variation("weight_elevated", "наше правило; база 0.25", (0.0, 0.1, 0.25, 0.5, 1.0)),
    Variation("equivalence_tolerance_hours", "наше правило; база 0.25 ч", (0.1, 0.25, 0.5, 1.0)),
)


def window_text(window: Window | None) -> str:
    return "нет" if window is None else f"{window.start:%H:%M}–{window.end:%H:%M}"


def evaluate(case: Case, lines: list[LineAssessment], rules: WindowRules) -> Outcome:
    scores = [score(w, lines, rules) for w in candidates(case.search.start, case.search.end, DURATION, rules)]
    requested = score(Window(start=case.eva_start, end=case.eva_start + DURATION), lines, rules)
    rec = recommend(scores, rules, requested=requested)
    chosen = next((s for s in scores if s.window == rec.preferred), None)
    return Outcome(
        preferred=window_text(rec.preferred),
        requested_minutes=exposure_hours(requested) * 60.0,
        preferred_minutes=0.0 if chosen is None else exposure_hours(chosen) * 60.0,
        excluded=sum(1 for s in scores if s.excluded_reason),
        total=len(scores),
    )


def trajectory_of(case: Case, asof: AsOf) -> Trajectory:
    found = oem_for(asof, case.search)
    if found is None:
        raise RuntimeError(f"нет эфемериды для случая: {case.title}")
    eph, rec = found
    return from_oem(eph, provenance_of(rec), case.search, is_reconstruction=case.cutoff is None)


def row(value: float, base: bool, outcome: Outcome, baseline: Outcome) -> str:
    same = "да" if outcome.preferred == baseline.preferred else "НЕТ"
    mark = " (база)" if base else ""
    return (
        f"| {value:g}{mark} | {outcome.preferred} | {same} | {outcome.requested_minutes:.0f} | "
        f"{outcome.preferred_minutes:.0f} | {outcome.excluded} из {outcome.total} |"
    )


def run_case(case: Case, store: RawStore, sep_cfg: SepConfig, rules: WindowRules) -> tuple[list[str], int, int]:
    asof = AsOf(store, case.cutoff)
    trajectory = trajectory_of(case, asof)
    meteors = MeteorLine(load_meteor_config()).assess(case.search, trajectory, asof)
    base_sep = SepLine(sep_cfg).assess(case.search, trajectory, asof)
    baseline = evaluate(case, [base_sep, meteors], rules)
    out = [
        f"## {case.title}",
        "",
        f"База: предпочтительное окно {baseline.preferred}, минут high+critical в запрошенном окне "
        f"{baseline.requested_minutes:.0f}, в предпочтительном {baseline.preferred_minutes:.0f}.",
        "",
    ]
    changed = total = 0
    header = [
        "| значение | предпочтительное окно | совпало с базой | минут high+critical в запрошенном | "
        "в предпочтительном | исключено окон |",
        "|---|---|---|---|---|---|",
    ]
    for variation in SEP_VARIATIONS:
        out += [f"### {variation.parameter}: {variation.basis}", "", *header]
        base_value = getattr(sep_cfg, variation.parameter)
        for value in variation.values:
            cfg = sep_cfg.model_copy(update={variation.parameter: value})
            sep = SepLine(cfg).assess(case.search, trajectory, asof)
            outcome = evaluate(case, [sep, meteors], rules)
            out.append(row(value, value == base_value, outcome, baseline))
            total += 1
            changed += outcome.preferred != baseline.preferred
        out.append("")
    for variation in RULE_VARIATIONS:
        out += [f"### {variation.parameter}: {variation.basis}", "", *header]
        base_value = getattr(rules, variation.parameter)
        for value in variation.values:
            varied = rules.model_copy(update={variation.parameter: value})
            outcome = evaluate(case, [base_sep, meteors], varied)
            out.append(row(value, value == base_value, outcome, baseline))
            total += 1
            changed += outcome.preferred != baseline.preferred
        out.append("")
    return out, changed, total


def main() -> int:
    parser = argparse.ArgumentParser(description="Чувствительность рекомендации к параметрам без первоисточника")
    parser.add_argument("--out", type=Path, default=Path("docs/SENSITIVITY.md"))
    args = parser.parse_args()
    store = RawStore(Settings().raw_root)
    sep_cfg, rules = load_sep_config(), load_window_rules()
    body: list[str] = []
    changed = total = 0
    for case in CASES:
        lines, case_changed, case_total = run_case(case, store, sep_cfg, rules)
        body += lines
        changed += case_changed
        total += case_total
        print(f"{case.title}: изменений рекомендации {case_changed} из {case_total}")
    head = [
        "# Чувствительность рекомендации к параметрам без первоисточника",
        "",
        f"Прогон {datetime.now(UTC):%Y-%m-%d %H:%M} UTC на сыром хранилище зеркала, `scripts/sensitivity.py`. "
        "По одному меняется параметр, остальные базовые; окно 6 ч, кандидаты каждые 15 мин. Рекомендация изменилась в "
        f"{changed} вариантах из {total}.",
        "",
    ]
    args.out.write_text("\n".join(head + body), encoding="utf-8")
    print(f"отчёт: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
