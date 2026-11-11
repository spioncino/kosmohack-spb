from datetime import timedelta
from pathlib import Path

from conftest import hours, interval, line, make_result, moment, window_score
from evarisk.api.schemas import AssessmentResult
from evarisk.briefing.facts import build_facts
from evarisk.briefing.llm import facts_sheet
from evarisk.briefing.models import QUESTIONS
from evarisk.briefing.speech import to_speech
from evarisk.briefing.templates import CLOSING, render_template
from evarisk.briefing.validate import MAX_WORDS, validate, words
from evarisk.types import Recommendation, Window, WindowScore

FIXTURE = Path("tests/fixtures/assessments/review_2024-05-10.json")
# Фикстура это сохранённый ответ на разбор 10 мая 2024: запрошенное окно 14:00–20:00, рекомендовано 13:00–19:00,
# у линии протонов один отрезок high 17:18–17:58, период поиска 13:00–22:00.
STORED_SOURCES = {"b95457c8c075c4fdeb97d57a", "32244c01a48c56ebfabac6ad", "aceeea516be20d00fed5f4d4"}


def stored() -> AssessmentResult:
    return AssessmentResult.model_validate_json(FIXTURE.read_text(encoding="utf-8"))


def test_facts_of_stored_review_repeat_the_document() -> None:
    facts = build_facts(stored(), "summary")
    assert facts.mode == "review"
    assert facts.verdict == "move_to"
    assert (facts.requested.start.hour, facts.requested.end.hour) == (14, 20)
    assert facts.requested.exposure_minutes == 40.0
    assert facts.requested.unknown_minutes == 0.0
    assert facts.recommended is not None
    assert (facts.recommended.start.hour, facts.recommended.end.hour) == (13, 19)
    assert facts.recommended.exposure_minutes == 40.0
    assert [(item.line, item.level, item.minutes) for item in facts.exposed] == [("sep_protons", "high", 40.0)]
    assert (facts.exposed[0].start.hour, facts.exposed[0].start.minute) == (17, 18)
    assert facts.clear_after is not None
    assert (facts.clear_after.hour, facts.clear_after.minute) == (17, 58)
    assert facts.gaps == ()
    assert facts.disabled_sources == ()
    assert facts.confidence_grade == "medium"
    assert {p.raw_id for p in facts.provenance} == STORED_SOURCES
    assert facts.windows_total == 13
    assert facts.windows_excluded == 0


def test_allowed_values_hold_every_number_of_the_facts() -> None:
    facts = build_facts(stored(), "summary")
    for value in ("14:00", "20:00", "13:00", "19:00", "17:18", "17:58", "40", "6", "13", "2024-05-10"):
        assert value in facts.allowed_values
    # Длительности посчитаны в фактах, а не моделью (D78): окно 6 ч, сдвиг начала на 1 ч раньше.
    assert "6 ч" in facts.allowed_values and "1 ч" in facts.allowed_values and "60" in facts.allowed_values


def keep_requested() -> AssessmentResult:
    requested = window_score(moment(4), moment(10), hours(none=5.0, high=1.0))
    return make_result(requested=requested, windows=(requested,))


def move_to() -> AssessmentResult:
    requested = window_score(moment(4), moment(10), hours(none=5.0, high=1.0))
    better = window_score(moment(8), moment(14), hours(none=6.0))
    protons = line(intervals=(interval(moment(4, 39), moment(5, 39), "high"),))
    return make_result(
        lines=(protons,),
        requested=requested,
        windows=(requested, better),
        recommendation=Recommendation(
            preferred=Window(start=moment(8), end=moment(14)),
            reason="текст причины в фактах не разбирается",
            equivalents=(),
            insufficient=False,
            rules_applied=(),
        ),
    )


def insufficient() -> AssessmentResult:
    requested = window_score(moment(4), moment(10), hours(unknown=6.0), excluded="недостаточно данных")
    return make_result(
        lines=(line(intervals=(), availability="missing"),),
        requested=requested,
        windows=(requested,),
        recommendation=Recommendation(
            preferred=None,
            reason="оснований для выбора нет",
            equivalents=(),
            insufficient=True,
            rules_applied=(),
        ),
    )


def test_verdict_keep_requested_when_preferred_equals_requested() -> None:
    facts = build_facts(keep_requested(), "summary")
    assert facts.verdict == "keep_requested"
    assert facts.requested.exposure_minutes == 60.0
    assert facts.recommended is not None
    assert facts.recommended.start == facts.requested.start


def test_verdict_move_to_carries_both_windows_and_exposed_intervals() -> None:
    facts = build_facts(move_to(), "summary")
    assert facts.verdict == "move_to"
    assert facts.recommended is not None
    assert facts.recommended.start == moment(8)
    assert facts.recommended.exposure_minutes == 0.0
    assert [(item.start, item.end, item.minutes) for item in facts.exposed] == [(moment(4, 39), moment(5, 39), 60.0)]
    assert facts.clear_after == moment(5, 39)


def test_verdict_insufficient_names_gaps_and_excluded_windows() -> None:
    facts = build_facts(insufficient(), "summary")
    assert facts.verdict == "insufficient"
    assert facts.recommended is None
    assert [(gap.line, gap.availability) for gap in facts.gaps] == [("sep_protons", "missing")]
    assert facts.requested.unknown_minutes == 360.0
    assert facts.requested.exposure_minutes == 0.0
    assert facts.windows_excluded == 1
    assert facts.exposed == ()
    assert facts.clear_after is None


def test_exposure_is_none_without_lines_in_the_comparison() -> None:
    empty = window_score(moment(4), moment(10), hours())
    result = make_result(requested=empty.model_copy(update={"per_line": {}}))
    facts = build_facts(result, "summary")
    assert facts.requested.exposure_minutes is None
    assert facts.requested.unknown_minutes is None


def test_disabled_sources_and_cutoff_reach_the_facts() -> None:
    disabled = "источники отключены пользователем и в расчёте не участвуют: goes_sgps, gfz_kp; линия без данных"
    facts = build_facts(make_result(limitations=(disabled,)), "summary")
    assert facts.disabled_sources == ("goes_sgps", "gfz_kp")
    assert facts.cutoff == moment(12, 30, day=10)
    assert "по данным на" in facts.basis


def test_template_of_every_question_passes_the_check_of_numbers() -> None:
    for result in (stored(), keep_requested(), move_to(), insufficient()):
        for question in QUESTIONS:
            facts = build_facts(result, question)
            text = render_template(facts)
            assert validate(text, facts) == []
            assert words(text) <= MAX_WORDS
            assert text.endswith(CLOSING)


def test_review_and_replay_templates_name_the_basis_of_data() -> None:
    review = build_facts(stored(), "summary")
    assert "Это разбор по архиву, а не прогноз." in render_template(review)
    replay = build_facts(keep_requested(), "summary")
    assert "Прогноз по данным на 2024-05-10 12:30 UTC." in render_template(replay)


def test_clear_after_is_empty_when_exposure_reaches_the_end_of_the_period() -> None:
    requested = window_score(moment(4), moment(10), hours(none=5.0, high=1.0))
    protons = line(intervals=(interval(moment(13), moment(14), "high"),))
    facts = build_facts(make_result(lines=(protons,), requested=requested, windows=(requested,)), "summary")
    assert facts.clear_after is None
    assert facts.exposed == ()
    assert facts.requested.start + timedelta(hours=6) == facts.requested.end


def test_move_on_elevated_minutes_names_them_and_the_direction() -> None:
    # Случай теста страницы 19 сентября: разбор 10 мая, запрошено 13:00–19:00, рекомендовано 10:45–16:45;
    # высокого и критического уровня нет в обоих окнах, в запрошенном 282 мин elevated, в рекомендованном 147.
    requested = window_score(moment(13, day=10), moment(19, day=10), hours(none=1.3, elevated=4.7))
    preferred = window_score(moment(10, 45, day=10), moment(16, 45, day=10), hours(none=3.55, elevated=2.45))
    result = make_result(
        mode="review",
        eva_start=moment(13, day=10),
        search_start=moment(10, day=10),
        search_end=moment(20, day=10),
        requested=requested,
        windows=(requested, preferred),
        recommendation=Recommendation(
            preferred=preferred.window, reason="причина", equivalents=(), insufficient=False, rules_applied=()
        ),
    )
    for question in QUESTIONS:
        facts = build_facts(result, question)
        assert facts.requested.elevated_minutes == 282 and facts.recommended is not None
        assert facts.recommended.elevated_minutes == 147
        text = render_template(facts)
        # Речевой стиль (D77, D78): числа в начальной форме, длительности по-человечески из фактов, смысл
        # повышенного фона по линии, без кодов уровней. 282 мин это 4 ч 42 мин, 147 мин это 2 ч 27 мин.
        assert "Опасных минут нет ни в запрошенном окне, ни в новом." in text, question
        assert "Зато по протонам повышенного фона в новом 2 ч 27 мин, а в запрошенном 4 ч 42 мин." in text
        assert "Повышенный фон значит, что протонов больше обычного, но опасная часть до экипажа не доходит." in text
        assert not any(code in text for code in ("elevated", "high", "critical", "штраф", ";", "вместо")), question
        assert validate(text, facts) == [], (question, validate(text, facts))
    summary = render_template(build_facts(result, "summary"))
    assert summary.startswith("Рекомендуем начать выход на 2 ч 15 мин раньше, в 10:45 UTC.")
    assert "а в запрошенном четыре часа сорок две минуты" in to_speech(summary)
    when = render_template(build_facts(result, "when_better"))
    assert when.startswith("Лучше начать на 2 ч 15 мин раньше, в 10:45 UTC, и закончить в 16:45.")


def test_no_window_because_of_danger_is_not_called_a_lack_of_data() -> None:
    # Все окна исключены критическим уровнем и ограничением по свету, данных хватает. До D77 сводка говорила
    # «данных недостаточно» и «запрошенное окно оценить нельзя», хотя окно оценено и не подходит.
    critical = "critical по линии sep_protons: 0.50 ч"
    dark = "ограничение освещённости: доля света 0.40 < 0.60"
    requested = window_score(
        moment(4), moment(10), {**hours(none=5.5), "critical": 0.5}, excluded=f"{critical}; {dark}"
    )
    other = window_score(moment(6), moment(12), hours(none=6.0), excluded=dark)
    result = make_result(
        mode="review",
        requested=requested,
        windows=(requested, other),
        recommendation=Recommendation(
            preferred=None, reason="причина", equivalents=(), insufficient=True, rules_applied=()
        ),
    )
    facts = build_facts(result, "summary")
    assert [(e.kind, e.line, e.windows) for e in facts.exclusions] == [
        ("light", None, 2),
        ("critical", "sep_protons", 1),
    ]
    text = render_template(facts)
    # Без канцелярита (D78): не «исключено окон 2 из 2», а «исключены все 2 окна»; число в начальной форме.
    assert text.startswith(
        "Нет оснований рекомендовать окно, все окна в периоде исключены. Запрошенное окно с 04:00 до 10:00 UTC не "
        "подходит, в нём критический уровень протонов и мало света. Из-за нехватки света исключены все 2 окна. "
        "Из-за критического уровня протонов исключено 1 окно."
    )
    assert "недостаточно" not in text and "оценить нельзя" not in text
    assert validate(text, facts) == []
    assert "исключено одно окно" in to_speech(text)


def unmeasured_flux(grade: str = "low", minutes: float | None = 414.0) -> AssessmentResult:
    """Текущий расчёт, где поток протонов не измерен на части периода поиска: уровень взят из прогноза."""
    protons = line(grade=grade).model_copy(
        update={
            "confidence": line(grade=grade).confidence.model_copy(
                update={"depends_on": "после отсечения уровень или доступ опираются на прогноз Kp SWPC (D39)"}
            ),
            "metrics": {} if minutes is None else {"unmeasured_minutes": minutes},
        }
    )
    return make_result(lines=(protons,), mode="current")


def test_unmeasured_flux_is_a_data_gap_and_never_sits_next_to_no_gaps() -> None:
    # Аудит 19 сентября: сводка говорила «Пробелов данных нет.», а следом «Поток протонов на эти минуты не измерен.».
    result = unmeasured_flux()
    for question in QUESTIONS:
        facts = build_facts(result, question)
        assert facts.unmeasured_minutes == 414.0 and facts.search_minutes == 840.0
        text = render_template(facts)
        assert "Поток протонов не измерен на части периода, всего 6 ч 54 мин" in text, question
        assert "Пробелов" not in text and text.count("не измерен") == 1, text
        assert "Уверенность в оценке протонов низкая." in text
        assert validate(text, facts) == [], validate(text, facts)
    sheet = facts_sheet(build_facts(result, "summary"))
    assert "Пробелы данных: нет" not in sheet and "Пробелы данных: Поток протонов не измерен" in sheet


def test_unmeasured_flux_named_by_the_confidence_alone_is_a_data_gap_too() -> None:
    result = unmeasured_flux(minutes=None)
    protons = result.lines[0]
    confidence = protons.confidence.model_copy(update={"depends_on": "измерений потока в окне нет (D42)"})
    result = result.model_copy(update={"lines": [protons.model_copy(update={"confidence": confidence})]})
    facts = build_facts(result, "what_blocks")
    text = render_template(facts)
    assert "Поток протонов не измерен, уровень взят из прогнозов и сообщений о событиях." in text
    assert "Пробелов" not in text and text.count("не измерен") == 1
    assert validate(text, facts) == []


def test_measured_flux_without_gaps_says_there_are_none() -> None:
    facts = build_facts(unmeasured_flux(grade="high", minutes=0.0), "what_blocks")
    text = render_template(facts)
    assert "Пробелов в данных нет." in text and "не измерен" not in text


def test_disabled_sources_are_named_in_russian_for_the_ear() -> None:
    disabled = "источники отключены пользователем и в расчёте не участвуют: goes_sgps, gfz_kp; линия без данных"
    facts = build_facts(make_result(limitations=(disabled,)), "summary")
    text = render_template(facts)
    assert "Вручную отключены спутниковые измерения протонов и индекс геомагнитной активности." in text
    assert "goes_sgps" not in text and to_speech(text)


def test_move_from_an_excluded_window_names_why_it_does_not_fit() -> None:
    # Запрошенное окно исключено заданным ограничением света, опасных минут нет ни там, ни там: без причины
    # исключения сводка не объясняла бы перенос.
    dark = "ограничение освещённости: доля света 0.40 < 0.60"
    requested = window_score(moment(4), moment(10), hours(none=6.0), excluded=dark)
    better = window_score(moment(8), moment(14), hours(none=6.0))
    result = make_result(
        requested=requested,
        windows=(requested, better),
        recommendation=Recommendation(
            preferred=better.window, reason="причина", equivalents=(), insufficient=False, rules_applied=()
        ),
    )
    for question in QUESTIONS:
        facts = build_facts(result, question)
        text = render_template(facts)
        assert "В запрошенном окне мало света." in text, (question, text)
        assert validate(text, facts) == []


def two_lines_raised(verdict: str) -> AssessmentResult:
    """Форма разбора 8 июня 2024 (D84): протоны и метеорные потоки повышены всё запрошенное окно 06:00–12:00.

    Сумма по линиям 720 мин, то есть «12 ч» в окне 6 ч; в новом окне 01:00–07:00 протонов 237 мин, метеоров 360.
    """

    def two(start: int, end: int, protons: float) -> WindowScore:
        levels = {"sep_protons": hours(none=6.0 - protons, elevated=protons), "meteor_showers": hours(elevated=6.0)}
        return WindowScore(
            window=Window(start=moment(start, day=8), end=moment(end, day=8)), per_line=levels, completeness=1.0
        )

    requested, better = two(6, 12, 6.0), two(1, 7, 3.95)
    preferred = better.window if verdict == "move_to" else requested.window
    return make_result(
        mode="review",
        eva_start=moment(6, day=8),
        search_start=moment(0, day=8),
        search_end=moment(0, day=9),
        requested=requested,
        windows=(requested, better),
        recommendation=Recommendation(
            preferred=preferred, reason="причина", equivalents=(), insufficient=False, rules_applied=()
        ),
    )


def test_minutes_of_two_lines_are_named_per_line_and_never_summed() -> None:
    for verdict in ("move_to", "keep_requested"):
        result = two_lines_raised(verdict)
        for question in QUESTIONS:
            facts = build_facts(result, question)
            assert facts.requested.elevated_by_line == {"sep_protons": 360.0, "meteor_showers": 360.0}
            # Сумма по линиям остаётся для сравнения окон, но ни в текст, ни в разрешённые значения не попадает.
            assert facts.requested.elevated_minutes == 720.0
            for summed in ("12 ч", "720", "597", "9 ч 57 мин"):
                assert summed not in facts.allowed_values, (verdict, question, summed)
            text, sheet = render_template(facts), facts_sheet(facts)
            for summed in ("12 ч", "720 мин", "9 ч 57 мин"):
                assert summed not in text and summed not in sheet, (verdict, question, summed)
            assert validate(text, facts) == [], (verdict, question, validate(text, facts))
            assert "по протонам 6 ч, по метеорным потокам 6 ч" in sheet
    moved = render_template(build_facts(two_lines_raised("move_to"), "summary"))
    assert "Зато по протонам повышенного фона в новом 3 ч 57 мин, а в запрошенном 6 ч." in moved
    kept = render_template(build_facts(two_lines_raised("keep_requested"), "what_blocks"))
    assert "Повышенного фона в окне по протонам 6 ч, по метеорным потокам 6 ч, то есть" in kept


def test_summed_raised_minutes_of_the_model_are_rejected() -> None:
    # Текст 8 июня до D84: сумма двух линий, «12 ч» повышенного фона в окне 6 ч.
    facts = build_facts(two_lines_raised("move_to"), "summary")
    before = (
        "Рекомендуем начать выход на 5 ч раньше, в 01:00 UTC. Опасных минут нет ни в запрошенном окне, ни в новом. "
        "Зато повышенного фона в новом окне 9 ч 57 мин, а в запрошенном 12 ч. Это разбор по архиву, а не прогноз. "
        f"{CLOSING}"
    )
    violations = validate(before, facts)
    assert any(v.startswith("длительность 12 ч длиннее окна 6 ч") for v in violations), violations
    assert any(v.startswith("длительность 9 ч 57 мин длиннее окна 6 ч") for v in violations), violations


def test_zero_from_a_forecast_is_said_as_a_forecast_in_the_briefing() -> None:
    # Replay 8 июня 2024 с отсечением 00:30 (D84): поток не измерен всё окно, уровень взят из внешнего прогноза.
    note = "Kp: прогноз Kp; поток: внешний прогноз вероятности 1 %"
    protons = line(intervals=(interval(moment(0), moment(14), "none").model_copy(update={"note": note}),))
    requested = window_score(moment(4), moment(10), hours(none=6.0))
    result = make_result(lines=(protons,), requested=requested, windows=(requested,))
    for question in QUESTIONS:
        facts = build_facts(result, question)
        assert facts.requested.flux_unmeasured_minutes == 360.0
        text = render_template(facts)
        assert "По прогнозу опасных минут в нём нет, измерений потока нет." in text, question
        assert validate(text, facts) == [], (question, validate(text, facts))
    facts = build_facts(result, "summary")
    said = f"Рекомендуем оставить окно 04:00. Опасных минут в нём нет. По данным на 2024-05-10 12:30. {CLOSING}"
    assert any("«прогноз»" in v for v in validate(said, facts))
    assert "по прогнозу опасных минут нет" in facts_sheet(facts)
