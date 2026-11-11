from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from evarisk.types import Confidence, Interval, Level, LineAssessment, Provenance, Window, WindowScore
from evarisk.windows.compare import (
    Constraints,
    candidates,
    constraint_rules,
    load_window_rules,
    overlap_hours,
    penalty,
    recommend,
    score,
)
from evarisk.windows.measures import ILLUMINATION_NO_DATA, ILLUMINATION_SHADOW_START

T0 = datetime(2024, 5, 10, 0, 0, tzinfo=UTC)
PROV = Provenance(
    raw_id="r", source="s", product="p", published_at=T0, published_at_kind="exact", data_version="v", url="u"
)
RULES = load_window_rules()


def line(name: str, intervals: tuple[Interval, ...], availability: str = "available") -> LineAssessment:
    return LineAssessment(
        line=name,
        mechanism=name,
        intervals=intervals,
        availability=availability,  # type: ignore[arg-type]
        confidence=Confidence(grade="high", depends_on="t"),
        limitations=(),
    )


def iv(h0: float, h1: float, level: str) -> Interval:
    return Interval(
        start=T0 + timedelta(hours=h0),
        end=T0 + timedelta(hours=h1),
        level=level,  # type: ignore[arg-type]
        kind="computed",
        provenance=(PROV,),
        rule="r",
    )


def test_candidates_have_equal_duration_and_step() -> None:
    c = candidates(T0, T0 + timedelta(hours=8), timedelta(hours=6), RULES)
    assert len(c) == 9 and all(w.duration == timedelta(hours=6) for w in c)
    assert c[1].start - c[0].start == timedelta(minutes=15)
    assert candidates(T0, T0 + timedelta(hours=1), timedelta(hours=6), RULES) == []


def test_overlap_hours_by_level() -> None:
    w = Window(start=T0 + timedelta(hours=2), end=T0 + timedelta(hours=8))
    hours = overlap_hours(w, [iv(0, 3, "high"), iv(3, 5, "elevated"), iv(7, 12, "critical")])
    assert hours["high"] == 1.0 and hours["elevated"] == 2.0 and hours["critical"] == 1.0


def test_recommend_prefers_less_exposure_and_reports_rules() -> None:
    early = Window(start=T0, end=T0 + timedelta(hours=6))
    late = Window(start=T0 + timedelta(hours=6), end=T0 + timedelta(hours=12))
    sep = line("sep", (iv(1, 3, "high"), iv(6, 12, "none")))
    rec = recommend([score(early, [sep], RULES), score(late, [sep], RULES)], RULES)
    assert rec.preferred == late and not rec.insufficient and rec.equivalents == ()
    assert any("weight_high" in r or "1.0" in r for r in rec.rules_applied)


def test_critical_excludes_and_missing_data_excludes() -> None:
    w1 = Window(start=T0, end=T0 + timedelta(hours=6))
    w2 = Window(start=T0 + timedelta(hours=6), end=T0 + timedelta(hours=12))
    sep = line("sep", (iv(1, 2, "critical"), iv(6, 12, "none")))
    meteors_missing = line("meteors", (), availability="missing")
    s1, s2 = score(w1, [sep, meteors_missing], RULES), score(w2, [sep, meteors_missing], RULES)
    assert s1.excluded_reason is not None and "critical" in s1.excluded_reason
    assert s2.excluded_reason is not None and "meteors" in s2.excluded_reason
    rec = recommend([s1, s2], RULES)
    assert rec.insufficient and rec.preferred is None


def test_all_windows_excluded_gives_grouped_reasons_not_a_list_per_window() -> None:
    w1 = Window(start=T0, end=T0 + timedelta(hours=6))
    w2 = Window(start=T0 + timedelta(hours=1), end=T0 + timedelta(hours=7))
    w3 = Window(start=T0 + timedelta(hours=8), end=T0 + timedelta(hours=14))
    sep = line("sep", (iv(0, 0.5, "critical"), iv(0.5, 6.7, "none"), iv(6.7, 7, "critical"), iv(7, 24, "none")))
    meteors_missing = line("meteors", (), availability="missing")
    scores = [score(w, [sep, meteors_missing], RULES) for w in (w1, w2, w3)]
    rec = recommend(scores, RULES)
    assert rec.insufficient and rec.preferred is None
    assert rec.reason == (
        "оснований для выбора нет: все 3 окон исключены: critical по линии sep в 2 окнах из 3 (0.30–0.50 ч); "
        "нет данных по линии meteors (missing) в 3 окнах из 3"
    )


def test_equivalent_windows_are_reported_as_such() -> None:
    w1 = Window(start=T0, end=T0 + timedelta(hours=6))
    w2 = Window(start=T0 + timedelta(minutes=15), end=T0 + timedelta(hours=6, minutes=15))
    quiet = line("sep", (iv(0, 24, "none"),))
    rec = recommend([score(w1, [quiet], RULES), score(w2, [quiet], RULES)], RULES)
    assert rec.preferred == w1 and rec.equivalents == (w2,) and "равнозначны" in rec.reason


# Синтетическая освещённость: свет 0–3 ч, тень 3–6 ч, свет 6–24 ч.
ILLUMINATION = line("illumination", (iv(0, 3, "none"), iv(3, 6, "elevated"), iv(6, 24, "none")))
QUIET = line("sep", (iv(0, 24, "none"),))
HALF_SHADOW = Window(start=T0, end=T0 + timedelta(hours=6))
SHADOW_START = Window(start=T0 + timedelta(hours=3), end=T0 + timedelta(hours=9))
DAYLIGHT = Window(start=T0 + timedelta(hours=6), end=T0 + timedelta(hours=12))


def test_illumination_without_constraints_neither_excludes_nor_penalises() -> None:
    s = score(HALF_SHADOW, [QUIET], RULES, constraints=None, illumination=ILLUMINATION)
    assert s.excluded_reason is None and "illumination" not in s.per_line and s.completeness == 1.0
    s = score(HALF_SHADOW, [QUIET], RULES, constraints=Constraints(), illumination=ILLUMINATION)
    assert s.excluded_reason is None and "illumination" not in s.per_line
    assert constraint_rules(None) == () and constraint_rules(Constraints()) == ()


def test_min_sunlit_fraction_excludes_dark_window_with_measured_fraction() -> None:
    limit = Constraints(min_sunlit_fraction=0.6)
    s = score(HALF_SHADOW, [QUIET], RULES, constraints=limit, illumination=ILLUMINATION)
    assert s.excluded_reason == "ограничение освещённости: доля света 0.50 < 0.60"
    assert "illumination" not in s.per_line
    assert score(DAYLIGHT, [QUIET], RULES, constraints=limit, illumination=ILLUMINATION).excluded_reason is None
    # Доля ровно на пороге допустима.
    exact = Constraints(min_sunlit_fraction=0.5)
    assert score(HALF_SHADOW, [QUIET], RULES, constraints=exact, illumination=ILLUMINATION).excluded_reason is None
    rec = recommend(
        [score(w, [QUIET], RULES, constraints=limit, illumination=ILLUMINATION) for w in (HALF_SHADOW, DAYLIGHT)],
        RULES,
    )
    assert rec.preferred == DAYLIGHT and not rec.insufficient
    assert constraint_rules(limit) == (
        "ограничение освещённости: доля света в окне не ниже 0.60 (задано пользователем, в штрафе не участвует)",
    )


def test_start_in_daylight_excludes_shadow_start() -> None:
    limit = Constraints(start_in_daylight=True)
    s = score(SHADOW_START, [QUIET], RULES, constraints=limit, illumination=ILLUMINATION)
    assert s.excluded_reason == ILLUMINATION_SHADOW_START == "ограничение освещённости: старт в тени"
    assert score(DAYLIGHT, [QUIET], RULES, constraints=limit, illumination=ILLUMINATION).excluded_reason is None
    both = Constraints(min_sunlit_fraction=0.9, start_in_daylight=True)
    s = score(SHADOW_START, [QUIET], RULES, constraints=both, illumination=ILLUMINATION)
    assert s.excluded_reason == "ограничение освещённости: доля света 0.50 < 0.90; " + ILLUMINATION_SHADOW_START
    assert len(constraint_rules(both)) == 2 and "старт на свету" in constraint_rules(both)[1]


def test_constraint_without_illumination_data_excludes_rather_than_passes() -> None:
    limit = Constraints(min_sunlit_fraction=0.1, start_in_daylight=True)
    assert score(DAYLIGHT, [QUIET], RULES, constraints=limit).excluded_reason == ILLUMINATION_NO_DATA
    missing = line("illumination", (), availability="missing")
    s = score(DAYLIGHT, [QUIET], RULES, constraints=limit, illumination=missing)
    assert s.excluded_reason == ILLUMINATION_NO_DATA
    # Интервалы не покрывают окно: доля не определена, старт без интервала.
    outside = Window(start=T0 + timedelta(hours=30), end=T0 + timedelta(hours=36))
    s = score(outside, [QUIET], RULES, constraints=limit, illumination=ILLUMINATION)
    assert s.excluded_reason == ILLUMINATION_NO_DATA


def test_constraints_reject_fraction_outside_unit_interval() -> None:
    with pytest.raises(ValidationError):
        Constraints(min_sunlit_fraction=1.01)
    with pytest.raises(ValidationError):
        Constraints(min_sunlit_fraction=-0.1)
    assert Constraints(min_sunlit_fraction=1.0).any_set and not Constraints().any_set


def test_float_noise_in_hours_does_not_break_the_earliest_window_tie() -> None:
    # Часы одного уровня, просуммированные по разному разбиению интервалов, отличаются на 1e-15 ч.
    def scored(start_hour: float, elevated: float) -> WindowScore:
        w = Window(start=T0 + timedelta(hours=start_hour), end=T0 + timedelta(hours=start_hour + 6))
        hours: dict[Level, float] = {"none": 0.0, "elevated": elevated, "high": 0.0, "critical": 0.0, "unknown": 0.0}
        return WindowScore(window=w, per_line={"sep_protons": hours}, completeness=1.0)

    noisy = [scored(0.0, 6.000000000000001), scored(0.25, 6.0), scored(0.5, 5.999999999999999)]
    rec = recommend(noisy, RULES)
    assert rec.preferred is not None and rec.preferred.start == T0
    assert len(rec.equivalents) == 2 and "самое раннее" in rec.reason
    # Настоящая разница штрафа по-прежнему ранжирует.
    rec = recommend([scored(0.0, 6.0), scored(0.25, 2.0)], RULES)
    assert rec.preferred is not None and rec.preferred.start == T0 + timedelta(hours=0.25)


def test_requested_window_is_kept_when_equivalent_to_best() -> None:
    w1 = Window(start=T0, end=T0 + timedelta(hours=6))
    w2 = Window(start=T0 + timedelta(hours=4), end=T0 + timedelta(hours=10))
    quiet = line("sep", (iv(0, 24, "none"),))
    scores = [score(w1, [quiet], RULES), score(w2, [quiet], RULES)]
    rec = recommend(scores, RULES, requested=scores[1])
    assert rec.preferred == w2 and rec.equivalents == (w1,) and "перенос не улучшает" in rec.reason


def test_requested_window_is_replaced_when_worse() -> None:
    w1 = Window(start=T0, end=T0 + timedelta(hours=6))
    w2 = Window(start=T0 + timedelta(hours=4), end=T0 + timedelta(hours=10))
    sep = line("sep", (iv(0, 4, "none"), iv(4, 10, "high"), iv(10, 24, "none")))
    scores = [score(w1, [sep], RULES), score(w2, [sep], RULES)]
    rec = recommend(scores, RULES, requested=scores[1])
    assert rec.preferred == w1 and "запрошенное окно хуже" in rec.reason and "D49" in rec.reason


def test_exposure_beyond_one_grid_step_is_not_equivalent_even_within_penalty_tolerance() -> None:
    # D48: 18 минут high на фоне elevated дают разницу штрафа 0.225 ч (< допуска 0.25), но разница часов
    # high+critical 0.30 ч больше шага сетки 0.25 ч, поэтому запрошенное окно не равнозначно лучшему.
    w1 = Window(start=T0, end=T0 + timedelta(hours=6))
    w2 = Window(start=T0 + timedelta(hours=6), end=T0 + timedelta(hours=12))
    sep = line("sep", (iv(0, 1, "elevated"), iv(1, 1.3, "high"), iv(1.3, 12, "elevated")))
    scores = [score(w1, [sep], RULES), score(w2, [sep], RULES)]
    assert penalty(scores[0], RULES) - penalty(scores[1], RULES) == pytest.approx(0.225)
    rec = recommend(scores, RULES, requested=scores[0])
    assert (
        rec.preferred == w2
        and rec.equivalents == ()
        and "линия-минут high+critical (сумма по линиям) 18 против 0" in rec.reason
    )
    assert any("D79" in r for r in rec.rules_applied)


def test_exposure_within_one_grid_step_is_not_equivalent() -> None:
    # D79: 12 минут high меньше шага сетки и разница штрафа 0.15 ч в пределах допуска, но окно без таких минут
    # лучше: запрошенное окно не сохраняется, перенос предлагается.
    w1 = Window(start=T0, end=T0 + timedelta(hours=6))
    w2 = Window(start=T0 + timedelta(hours=6), end=T0 + timedelta(hours=12))
    sep = line("sep", (iv(0, 1, "elevated"), iv(1, 1.2, "high"), iv(1.2, 12, "elevated")))
    scores = [score(w1, [sep], RULES), score(w2, [sep], RULES)]
    assert penalty(scores[0], RULES) - penalty(scores[1], RULES) == pytest.approx(0.15)
    rec = recommend(scores, RULES, requested=scores[0])
    assert rec.preferred == w2 and rec.equivalents == () and "перенос не улучшает" not in rec.reason
    assert "линия-минут high+critical (сумма по линиям) 12 против 0" in rec.reason


def test_seven_dangerous_minutes_move_the_window_to_the_clean_one_a_grid_step_later() -> None:
    # Случай аудита, replay 11 мая 2024: запрошенное окно 09:15–15:15 содержит 7 мин high (виток доступа
    # 09:21–09:28) на фоне elevated, окно на шаг сетки позже 09:30–15:30 таких минут не содержит.
    sep = line("sep", (iv(0, 9.35, "elevated"), iv(9.35, 9 + 28 / 60, "high"), iv(9 + 28 / 60, 24, "elevated")))
    windows = [Window(start=T0 + timedelta(hours=h), end=T0 + timedelta(hours=h + 6)) for h in (9.25, 9.5, 9.75)]
    scores = [score(w, [sep], RULES) for w in windows]
    rec = recommend(scores, RULES, requested=scores[0])
    assert rec.preferred == windows[1] and rec.equivalents == (windows[2],)
    assert "запрошенное окно хуже: линия-минут high+critical (сумма по линиям) 7 против 0" in rec.reason
    assert "перенос не улучшает" not in rec.reason


def test_rules_applied_state_the_critical_rule_in_words() -> None:
    sep = line("sep", (iv(0, 24, "none"),))
    rec = recommend([score(Window(start=T0, end=T0 + timedelta(hours=6)), [sep], RULES)], RULES)
    assert "окно с critical исключается" in rec.rules_applied
    assert not any("True" in rule or "False" in rule for rule in rec.rules_applied)


def test_requested_window_excluded_does_not_become_preferred() -> None:
    w1 = Window(start=T0, end=T0 + timedelta(hours=6))
    w2 = Window(start=T0 + timedelta(hours=4), end=T0 + timedelta(hours=10))
    sep = line("sep", (iv(0, 8, "none"), iv(8, 9, "critical"), iv(9, 24, "none")))
    scores = [score(w1, [sep], RULES), score(w2, [sep], RULES)]
    rec = recommend(scores, RULES, requested=scores[1])
    assert rec.preferred == w1 and scores[1].excluded_reason is not None


def test_move_goes_to_the_clean_window_closest_to_the_requested_start() -> None:
    # D49: открытые минуты (high) лежат до 8 ч; окна со стартом 8, 9 и 10 ч чистые и равны по штрафу.
    # Запрошенное 4–10 ч содержит 18 мин high, перенос идёт на ближайшее чистое окно 8–14 ч, а не на самое позднее
    # или случайное из равнозначных.
    sep = line("sep", (iv(0, 5, "elevated"), iv(5, 5.3, "high"), iv(5.3, 24, "elevated")))
    windows = [Window(start=T0 + timedelta(hours=h), end=T0 + timedelta(hours=h + 6)) for h in (4, 8, 9, 10)]
    scores = [score(w, [sep], RULES) for w in windows]
    rec = recommend(scores, RULES, requested=scores[0])
    assert rec.preferred == windows[1] and set(rec.equivalents) == {windows[2], windows[3]}
    assert (
        "ближайшее к запрошенному из 3 окон" in rec.reason
        and "линия-минут high+critical (сумма по линиям) 18 против 0" in rec.reason
    )


def test_homogeneous_period_says_no_window_is_better() -> None:
    sep = line("sep", (iv(0, 24, "elevated"),))
    windows = [Window(start=T0 + timedelta(hours=h), end=T0 + timedelta(hours=h + 6)) for h in (0, 4, 8)]
    scores = [score(w, [sep], RULES) for w in windows]
    rec = recommend(scores, RULES, requested=scores[1])
    assert rec.preferred == windows[1] and "обстановка на периоде поиска однородна" in rec.reason


def test_completeness_is_the_share_of_minutes_with_a_known_level() -> None:
    # Линия доступна, но 2 из 6 часов окна без уровня: полнота 4/6, а не 100 %.
    w = Window(start=T0, end=T0 + timedelta(hours=6))
    sep = line("sep", (iv(0, 2, "unknown"), iv(2, 6, "none")))
    assert score(w, [sep], RULES).completeness == pytest.approx(4 / 6)
    meteors = line("meteors", (iv(0, 6, "none"),))
    assert score(w, [sep, meteors], RULES).completeness == pytest.approx((4 / 6 + 1.0) / 2)
    missing = line("meteors", (), availability="missing")
    assert score(w, [sep, missing], RULES).completeness == pytest.approx((4 / 6) / 2)


def test_gap_within_a_grid_step_keeps_the_window_comparable() -> None:
    # Пробел 12 мин короче шага сетки: окно не исключается и остаётся равнозначным полностью известному по D56
    # (разница часов без уровня не больше шага), поэтому запрошенное окно сохраняется (D45).
    sep = line("sep", (iv(0, 0.2, "unknown"), iv(0.2, 12, "elevated")))
    early = Window(start=T0, end=T0 + timedelta(hours=6))
    late = Window(start=T0 + timedelta(hours=6), end=T0 + timedelta(hours=12))
    scores = [score(early, [sep], RULES), score(late, [sep], RULES)]
    assert scores[0].excluded_reason is None
    rec = recommend(scores, RULES, requested=scores[0])
    assert rec.preferred == early and "перенос не улучшает" in rec.reason


def test_gap_longer_than_a_grid_step_excludes_the_window_instead_of_winning() -> None:
    # Окно с 45 мин high и окно, где 225 из 240 минут без уровня: пропуск данных не равен нулевому риску, поэтому
    # второе окно исключается с названной причиной, а не выигрывает по минутам high+critical (D70).
    sep = line("sep", (iv(0, 0.75, "high"), iv(0.75, 4, "none"), iv(4, 7.75, "unknown"), iv(7.75, 8, "none")))
    known = Window(start=T0, end=T0 + timedelta(hours=4))
    gap = Window(start=T0 + timedelta(hours=4), end=T0 + timedelta(hours=8))
    scores = [score(known, [sep], RULES), score(gap, [sep], RULES)]
    assert scores[1].excluded_reason == "пробел данных по линии sep: 225 мин без уровня (D70)"
    rec = recommend(scores, RULES, requested=scores[1])
    assert rec.preferred == known and not rec.insufficient
    assert any("D70" in rule for rule in rec.rules_applied)
    # Пробел ровно в шаг сетки (15 мин) окно не исключает.
    edge = line("sep", (iv(0, 0.25, "unknown"), iv(0.25, 4, "none")))
    assert score(known, [edge], RULES).excluded_reason is None
    everything = recommend([scores[1]], RULES)
    assert everything.insufficient and "пробел данных по линии sep в 1 окнах из 1 (225 мин без уровня, D70)" in (
        everything.reason
    )


def test_window_starting_before_the_cutoff_is_excluded_as_already_past() -> None:
    # D80: при отсечении 03:00 окна со стартом 02:45 на момент прогноза уже в прошлом; окно со стартом ровно в
    # момент отсечения допустимо.
    sep = line("sep", (iv(0, 24, "none"),))
    cutoff = T0 + timedelta(hours=3)
    past = score(Window(start=T0 + timedelta(hours=2.75), end=T0 + timedelta(hours=8.75)), [sep], RULES, cutoff=cutoff)
    at_cutoff = score(Window(start=cutoff, end=cutoff + timedelta(hours=6)), [sep], RULES, cutoff=cutoff)
    assert past.excluded_reason == "окно начинается раньше момента отсечения"
    assert at_cutoff.excluded_reason is None
    rec = recommend([past, at_cutoff], RULES, requested=at_cutoff)
    assert rec.preferred == at_cutoff.window and rec.equivalents == ()
    everything = recommend([past], RULES)
    assert everything.insufficient and "окно начинается раньше момента отсечения в 1 окнах из 1" in everything.reason
