from datetime import UTC, datetime, timedelta

from evarisk.replay.verify import compare, exposure_counts, first_start, level_at, minute_counts
from evarisk.types import Interval, Level, Window

DAY = datetime(2024, 5, 11, tzinfo=UTC)
SPAN = Window(start=DAY + timedelta(hours=3), end=DAY + timedelta(hours=7))
CUTOFF = DAY + timedelta(hours=2)


def iv(h0: float, h1: float, level: Level, kind: str = "computed") -> Interval:
    return Interval(
        start=DAY + timedelta(hours=h0),
        end=DAY + timedelta(hours=h1),
        level=level,
        kind=kind,  # type: ignore[arg-type]
        provenance=(),
        rule="тест",
    )


# Прогноз: none 03–04, high 04–05, elevated 05–06, без уровня 06–07.
PREDICTED = (iv(3, 4, "none"), iv(4, 5, "high", "external_forecast"), iv(5, 6, "elevated"))
# Факт: none 03–04:30, high 04:30–05:30, critical 05:30–05:45, none 05:45–07.
OBSERVED = (iv(3, 4.5, "none"), iv(4.5, 5.5, "high"), iv(5.5, 5.75, "critical"), iv(5.75, 7, "none"))
WINDOW_A = Window(start=DAY + timedelta(hours=3), end=DAY + timedelta(hours=5))
WINDOW_B = Window(start=DAY + timedelta(hours=5), end=DAY + timedelta(hours=7))


def test_level_at_returns_unknown_outside_intervals() -> None:
    assert level_at(PREDICTED, DAY + timedelta(hours=4, minutes=30)) == "high"
    assert level_at(PREDICTED, DAY + timedelta(hours=5)) == "elevated"
    assert level_at(PREDICTED, DAY + timedelta(hours=6)) == "unknown"
    assert level_at(PREDICTED, DAY + timedelta(hours=2)) == "unknown"


def test_minute_counts_by_hand() -> None:
    c = minute_counts(PREDICTED, OBSERVED, SPAN)
    # Попадание: 04:30–05:00. Ложная тревога: 04:00–04:30. Пропуск: 05:00–05:45.
    assert c.hit == 30.0 and c.false_alarm == 30.0 and c.miss == 45.0
    # Спокойно у обоих: 03:00–04:00 и 05:45–07:00 (прогноз без уровня 06:00–07:00 тоже «не ≥ high»).
    assert c.quiet == 60.0 + 75.0
    assert c.unverified == 0.0 and c.predicted_unknown == 60.0
    assert c.hit + c.false_alarm + c.miss + c.quiet + c.unverified == 240.0


def test_minutes_without_fact_are_unverified_not_quiet() -> None:
    observed = (iv(3, 4.5, "none"), iv(4.5, 5.5, "high"))
    c = minute_counts(PREDICTED, observed, SPAN)
    assert c.hit == 30.0 and c.false_alarm == 30.0 and c.miss == 30.0
    assert c.unverified == 90.0 and c.quiet == 60.0


def test_first_start_is_clipped_to_span() -> None:
    early = (iv(1, 4, "high"), iv(4, 5, "none"))
    assert first_start(early, frozenset({"high", "critical"}), SPAN) == SPAN.start
    assert first_start((iv(1, 2.5, "high"),), frozenset({"high", "critical"}), SPAN) is None
    assert first_start(OBSERVED, frozenset({"high", "critical"}), SPAN) == DAY + timedelta(hours=4, minutes=30)


def test_lead_time_from_cutoff_to_first_observed_alarm() -> None:
    v = compare(
        CUTOFF,
        PREDICTED,
        OBSERVED,
        SPAN,
        predicted_preferred=WINDOW_A,
        observed_preferred=WINDOW_B,
        observed_insufficient=False,
        observed_availability="available",
    )
    assert v.first_observed_alarm == DAY + timedelta(hours=4, minutes=30)
    assert v.first_predicted_warning == DAY + timedelta(hours=4)
    assert v.lead_time_minutes == 150.0
    assert v.hit_minutes == 30.0 and v.miss_minutes == 45.0 and v.false_alarm_minutes == 30.0
    assert v.window_choice_stable is False and "не совпала" in v.note
    assert v.truth_asof is None and v.line == "sep_protons"
    assert "D41" in v.note and "D5" in v.note


def test_lead_time_undefined_when_forecast_warns_only_after_the_fact() -> None:
    late = (iv(3, 5, "none"), iv(5, 7, "high"))
    v = compare(
        CUTOFF,
        late,
        OBSERVED,
        SPAN,
        predicted_preferred=WINDOW_A,
        observed_preferred=WINDOW_A,
        observed_insufficient=False,
        observed_availability="available",
    )
    assert v.lead_time_minutes is None and "позже первого факта" in v.note
    assert v.hit_minutes == 30.0 + 15.0 and v.miss_minutes == 30.0 and v.false_alarm_minutes == 75.0
    assert v.window_choice_stable is True and "совпала с фактом" in v.note


def test_lead_time_undefined_without_any_warning_or_without_fact() -> None:
    quiet = (iv(3, 7, "none"),)
    v = compare(
        CUTOFF,
        quiet,
        OBSERVED,
        SPAN,
        predicted_preferred=WINDOW_A,
        observed_preferred=None,
        observed_insufficient=True,
        observed_availability="available",
    )
    assert v.lead_time_minutes is None and "ни elevated, ни high" in v.note
    assert v.miss_minutes == 75.0 and v.window_choice_stable is False
    none_observed = compare(
        CUTOFF,
        PREDICTED,
        (iv(3, 7, "none"),),
        SPAN,
        predicted_preferred=None,
        observed_preferred=None,
        observed_insufficient=True,
        observed_availability="available",
    )
    assert none_observed.lead_time_minutes is None and "не содержит интервалов ≥ high" in none_observed.note
    assert none_observed.false_alarm_minutes == 60.0 and none_observed.window_choice_stable is True
    assert "в обоих случаях оснований для выбора нет" in none_observed.note


def test_cutoff_after_alarm_onset_gives_no_lead_time() -> None:
    v = compare(
        DAY + timedelta(hours=5),
        PREDICTED,
        OBSERVED,
        SPAN,
        predicted_preferred=WINDOW_A,
        observed_preferred=WINDOW_A,
        observed_insufficient=False,
        observed_availability="available",
    )
    assert v.lead_time_minutes is None and "не позже отсечения" in v.note


def test_missing_fact_is_named_in_note() -> None:
    v = compare(
        CUTOFF,
        PREDICTED,
        (),
        SPAN,
        predicted_preferred=WINDOW_A,
        observed_preferred=None,
        observed_insufficient=True,
        observed_availability="missing",
    )
    assert v.unverified_minutes == 240.0 and v.hit_minutes == v.miss_minutes == v.false_alarm_minutes == 0.0
    assert "missing" in v.note and v.observed_availability == "missing"


def test_exposure_counts_are_set_intersections_inside_the_span() -> None:
    # Прогноз назвал минуты доступа 05:01–05:06, по факту протоны доставали станцию 05:03–05:08: три общие минуты
    # это попадания, 05:06–05:08 пропуски, 05:01–05:02 ложные тревоги; минута вне периода не считается.
    base = datetime(2025, 11, 12, 5, 0, tzinfo=UTC)
    span = Window(start=base, end=base + timedelta(hours=1))
    predicted = frozenset(base + timedelta(minutes=m) for m in (1, 2, 3, 4, 5))
    observed = frozenset(base + timedelta(minutes=m) for m in (3, 4, 5, 6, 7, 8, 75))
    assert exposure_counts(predicted, observed, span) == (3.0, 3.0, 2.0)
    assert exposure_counts(frozenset(), frozenset(), span) == (0.0, 0.0, 0.0)


def test_window_choice_is_stable_when_the_forecast_window_is_equivalent_by_fact() -> None:
    # D65: окно прогноза 08:00–14:00 не равно ближайшему по факту 09:15–15:15, но входит в равнозначные по факту.
    base = datetime(2024, 5, 11, 0, 0, tzinfo=UTC)
    span = Window(start=base, end=base + timedelta(hours=24))
    predicted = Window(start=base + timedelta(hours=8), end=base + timedelta(hours=14))
    by_fact = Window(start=base + timedelta(hours=9, minutes=15), end=base + timedelta(hours=15, minutes=15))
    common = {
        "predicted_preferred": predicted,
        "observed_preferred": by_fact,
        "observed_insufficient": False,
        "observed_availability": "available",
    }
    loose = compare(base, (), (), span, observed_equivalents=(predicted,), **common)
    strict = compare(base, (), (), span, observed_equivalents=(), **common)
    assert loose.window_choice_stable is True and "входит в число равнозначных по факту" in loose.note
    assert strict.window_choice_stable is False and "не совпала с фактом" in strict.note
