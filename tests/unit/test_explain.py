from datetime import UTC, datetime, timedelta

from evarisk.api.service import exposure_plain, exposure_spans, unscored_plain, with_notes
from evarisk.types import Confidence, Interval, LineAssessment, Provenance, Recommendation, Window
from evarisk.windows.compare import load_window_rules, recommend, score
from evarisk.windows.explain import NEAREST, explain, plain_reason, plural, span_text

T0 = datetime(2024, 5, 10, 0, 0, tzinfo=UTC)
PROV = Provenance(
    raw_id="r", source="s", product="p", published_at=T0, published_at_kind="exact", data_version="v", url="u"
)
RULES = load_window_rules()
# Коды уровней, линий и решений остаются в техническом `reason`; в объяснении для человека их нет (D76).
TECHNICAL = ("high", "critical", "elevated", "unknown", "sep_protons", "штраф", "D4", "D5", "D7")


def at(hours: float) -> datetime:
    return T0 + timedelta(hours=hours)


def window(h0: float, h1: float) -> Window:
    return Window(start=at(h0), end=at(h1))


def sep(*spans: tuple[float, float, str]) -> LineAssessment:
    return LineAssessment(
        line="sep_protons",
        mechanism="radiation",
        intervals=tuple(
            Interval(
                start=at(h0),
                end=at(h1),
                level=level,  # type: ignore[arg-type]
                kind="computed",
                provenance=(PROV,),
                rule="r",
            )
            for h0, h1, level in spans
        ),
        availability="available",
        confidence=Confidence(grade="high", depends_on="t"),
        limitations=(),
    )


def plain(text: tuple[str, ...]) -> bool:
    joined = " ".join(text)
    return not any(word in joined for word in TECHNICAL)


PROTONS_RAISED = (
    "Протонов больше обычного, но опасная часть до экипажа не доходит: станцию закрывает магнитное поле Земли или "
    "протонов высокой энергии мало."
)


def conjunctions_without_archive() -> LineAssessment:
    return LineAssessment(
        line="conjunctions",
        mechanism="collision",
        intervals=(),
        availability="unusable_for_replay",
        confidence=Confidence(grade="low", depends_on="архива нет"),
        limitations=(),
    )


def test_move_decided_by_raised_background_names_minutes_and_meaning() -> None:
    # Случай разбора 10 мая: запрошено 13:00–19:00, опасных минут нет нигде, повышенный фон протонов с 14:00.
    # В запрошенном окне его 300 мин, в окне 10:45–16:45 165 мин: перенос на 2 ч 15 мин раньше.
    line = sep((0, 14, "none"), (14, 24, "elevated"))
    scores = [score(window(10.75, 16.75), [line], RULES), score(window(13, 19), [line], RULES)]
    rec = recommend(scores, RULES, requested=scores[1])
    assert rec.preferred == window(10.75, 16.75)
    # Длительности по-человечески (D78): 300 мин это 5 ч, 165 мин это 2 ч 45 мин.
    assert rec.explanation == (
        "Рекомендуем перенести выход на 10:45–16:45 UTC, на 2 ч 15 мин раньше.",
        "Опасных минут нет ни в запрошенном, ни в новом окне.",
        "Зато в новом окне меньше повышенного фона:",
        f"Протоны: в запрошенном окне 5 ч, в новом 2 ч 45 мин. {PROTONS_RAISED}",
        NEAREST,
    )
    assert plain(rec.explanation)
    # Технический текст для аудита не меняется.
    assert "линия-минут elevated (сумма по линиям) 300 против 165" in rec.reason


def test_move_decided_by_dangerous_minutes() -> None:
    line = sep((0, 5, "elevated"), (5, 5.3, "high"), (5.3, 24, "elevated"))
    scores = [score(w, [line], RULES) for w in (window(4, 10), window(8, 14), window(9, 15))]
    rec = recommend(scores, RULES, requested=scores[0])
    assert rec.explanation[:3] == (
        "Рекомендуем перенести выход на 08:00–14:00 UTC, на 4 ч позже.",
        "В новом окне опасных минут нет:",
        "Протоны: в запрошенном окне 18 мин, в новом нет. Протоны высокой энергии достают станцию.",
    )
    assert rec.explanation[-1] == "Ещё 1 окно почти равноценно выбранному."
    assert plain(rec.explanation)


def test_more_raised_background_in_the_new_window_is_not_called_less() -> None:
    # Перенос решили минуты без данных: в новом окне повышенного фона больше, и объяснение не говорит «меньше».
    line = sep(
        (0, 1 / 6, "unknown"), (1 / 6, 7 / 6, "elevated"), (7 / 6, 6, "none"), (6, 8, "elevated"), (8, 12, "none")
    )
    requested, chosen = score(window(0, 6), [line], RULES), score(window(6, 12), [line], RULES)
    rec = Recommendation(preferred=chosen.window, reason="r", equivalents=(), insufficient=False, rules_applied=())
    text = explain([requested, chosen], rec, requested)
    assert "Зато в новом окне меньше повышенного фона:" not in text
    assert text[1:4] == (
        "Опасных минут нет ни в запрошенном, ни в новом окне.",
        "Повышенный фон в окнах:",
        f"Протоны: в запрошенном окне 1 ч, в новом 2 ч. {PROTONS_RAISED}",
    )
    assert text[4:6] == ("Минуты без данных:", "Протоны: в запрошенном окне 10 мин, в новом нет.")


def test_kept_window_says_the_move_gains_nothing_noticeable() -> None:
    line = sep((0, 24, "none"))
    scores = [score(w, [line], RULES) for w in (window(0, 6), window(4, 10), window(8, 14))]
    rec = recommend(scores, RULES, requested=scores[1])
    assert rec.explanation == (
        "Рекомендуем оставить запрошенное окно 04:00–10:00 UTC: перенос заметного выигрыша не даёт.",
        "Опасных минут в окне нет.",
        "Ещё 2 окна почти равноценны выбранному.",
    )


def test_kept_window_that_ends_at_midnight_is_named_without_dates() -> None:
    # Окно 18:00–00:00 кончается в полночь: до D78 объяснение выводило «2026-09-19 18:00 – 2026-09-20 00:00 UTC».
    line = sep((0, 48, "none"))
    scores = [score(w, [line], RULES) for w in (window(16, 22), window(18, 24))]
    rec = recommend(scores, RULES, requested=scores[1])
    assert rec.explanation[0] == (
        "Рекомендуем оставить запрошенное окно 18:00–00:00 UTC: перенос заметного выигрыша не даёт."
    )
    assert span_text(window(22, 28)) == "22:00 10 мая – 04:00 11 мая UTC"


def test_kept_window_with_as_many_dangerous_minutes_as_the_best_one() -> None:
    # В каждом окне по 12 минут уровня high: запрошенное окно не хуже лучшего, фраза верна при любом допуске.
    line = sep((0, 1, "elevated"), (1, 1.2, "high"), (1.2, 7, "elevated"), (7, 7.2, "high"), (7.2, 12, "elevated"))
    scores = [score(window(0, 6), [line], RULES), score(window(6, 12), [line], RULES)]
    rec = recommend(scores, RULES, requested=scores[0])
    assert rec.preferred == window(0, 6)
    assert rec.explanation[1:4] == (
        "Опасные минуты в окне, по каждой линии отдельно:",
        "Протоны: 12 мин. Протоны высокой энергии достают станцию.",
        "Меньше опасных минут нет ни в одном подходящем окне.",
    )
    assert "Повышенный фон в окне:" in rec.explanation and plain(rec.explanation)


def test_kept_window_with_more_dangerous_minutes_only_names_the_numbers() -> None:
    # По D79 такое окно не сохраняется; объяснение по готовой рекомендации называет числа и не ссылается на допуск.
    line = sep((0, 1, "elevated"), (1, 1.2, "high"), (1.2, 12, "elevated"))
    scores = [score(window(0, 6), [line], RULES), score(window(6, 12), [line], RULES)]
    rec = Recommendation(preferred=window(0, 6), reason="r", equivalents=(), insufficient=False, rules_applied=())
    text = explain(scores, rec, scores[0])
    assert "В лучших подходящих окнах их нет." in text
    assert not any("15 мин" in sentence for sentence in text)


def test_excluded_requested_window_gives_the_reason_in_words() -> None:
    line = sep((0, 8, "none"), (8, 9, "critical"), (9, 24, "none"))
    scores = [score(window(0, 6), [line], RULES), score(window(4, 10), [line], RULES)]
    rec = recommend(scores, RULES, requested=scores[1])
    assert rec.explanation == (
        "Рекомендуем перенести выход на 00:00–06:00 UTC, на 4 ч раньше.",
        "Запрошенное окно 04:00–10:00 UTC не подходит: критический уровень протонов, всего 1 ч.",
        "В новом окне опасных минут нет.",
    )


def test_no_window_lists_grouped_reasons() -> None:
    line = sep((0, 0.75, "high"), (0.75, 4, "unknown"), (4, 12, "critical"))
    scores = [score(w, [line], RULES) for w in (window(4, 8), window(6, 10))]
    rec = recommend(scores, RULES)
    assert rec.insufficient and rec.explanation == (
        "Подходящего окна нет: исключены все 2 окна.",
        "Во всех 2 окнах критический уровень протонов.",
    )
    single = recommend(scores[:1], RULES)
    assert single.explanation == (
        "Подходящего окна нет: единственное окно исключено.",
        "В нём критический уровень протонов.",
    )


def test_plain_reason_and_plural() -> None:
    assert plain_reason("недостаточно данных по линии conjunctions (missing)") == "нет данных по сближениям"
    assert plain_reason("недостаточно данных по линии sep_protons (stale)") == "данные по протонам устарели"
    assert (
        plain_reason("пробел данных по линии sep_protons: 45 мин без уровня (D70)")
        == "пробел данных по протонам, 45 мин"
    )
    assert plain_reason("ограничение освещённости: доля света 0.43 < 0.60; ограничение освещённости: старт в тени") == (
        "мало света: на свету 43% окна, а задано не меньше 60%; выход начинается в тени, а задан старт на свету"
    )
    assert plain_reason("незнакомая причина") == "незнакомая причина"
    forms = [plural(n, "окно", "окна", "окон") for n in (1, 3, 5, 11, 21, 22, 112)]
    assert forms == ["окно", "окна", "окон", "окон", "окно", "окна", "окон"]


def test_dangerous_spans_of_the_requested_window_in_words() -> None:
    line = sep((0, 4.5, "none"), (4.5, 4.8, "high"), (4.8, 5, "critical"), (5, 24, "none"))
    inside, last = exposure_spans(window(4, 10), window(0, 12), [line])
    assert exposure_plain(inside, last, window(0, 12)) == (
        "Опасные отрезки в запрошенном окне: высокий уровень протонов 04:30–04:48; критический уровень протонов "
        "04:48–05:00. После 05:00 UTC 10 мая опасных отрезков до конца периода поиска нет."
    )
    assert exposure_plain([], None, window(0, 12)) == ""


def test_lines_left_out_of_the_comparison_are_named() -> None:
    # Сближения без архива на момент данных в сравнении окон не участвуют (D30): объяснение говорит об этом прямо,
    # и фраза идёт перед выбором ближайшего окна и числом равноценных окон.
    conjunctions = conjunctions_without_archive()
    assert unscored_plain([conjunctions]) == (
        "Сближения в сравнении окон не участвуют: архива отчётов о сближениях за то время нет."
    )
    assert unscored_plain([]) == ""
    line = sep((0, 5, "elevated"), (5, 5.3, "high"), (5.3, 24, "elevated"))
    scores = [score(w, [line], RULES) for w in (window(4, 10), window(8, 14), window(9, 15))]
    rec = recommend(scores, RULES, requested=scores[0])
    noted = with_notes(rec, window(4, 10), window(0, 24), line, [line], [conjunctions])
    assert noted.explanation[-3:] == (
        "Сближения в сравнении окон не участвуют: архива отчётов о сближениях за то время нет.",
        NEAREST,
        "Ещё 1 окно почти равноценно выбранному.",
    )
    assert noted.explanation[-4].startswith("Опасные отрезки в запрошенном окне: высокий уровень протонов 05:00–05:18.")
    assert noted.reason.startswith(rec.reason)
    # Без подходящего окна фраза тоже звучит: сравнение окон шло без сближений.
    none = recommend([score(window(4, 10), [sep((0, 24, "critical"))], RULES)], RULES)
    blocked = with_notes(none, window(4, 10), window(0, 24), line, [line], [conjunctions])
    assert blocked.explanation[-1].startswith("Сближения в сравнении окон не участвуют")
    assert blocked.reason == none.reason


def test_two_lines_raised_all_window_are_explained_per_line() -> None:
    # Форма разбора 8 июня 2024 (D84): протоны и метеоры повышены всё окно 06:00–12:00, сумма по линиям 720 мин.
    # Объяснение называет минуты каждой линии, не больше длины окна; сумма остаётся в `reason` с пометкой.
    protons = sep((0, 3.05, "none"), (3.05, 24, "elevated"))
    meteors = sep((0, 24, "elevated")).model_copy(update={"line": "meteor_showers"})
    scores = [score(window(1, 7), [protons, meteors], RULES), score(window(6, 12), [protons, meteors], RULES)]
    rec = recommend(scores, RULES, requested=scores[1])
    assert rec.preferred == window(1, 7)
    assert "Протоны: в запрошенном окне 6 ч, в новом 3 ч 57 мин." in rec.explanation[3]
    assert not any(summed in s for s in rec.explanation for summed in ("12 ч", "720", "9 ч 57 мин"))
    assert "линия-минут elevated (сумма по линиям) 720 против 597" in rec.reason


def test_dangerous_minutes_of_two_lines_are_named_per_line() -> None:
    # Протоны и метеоры на уровне high одновременно час: в окне 1 ч, а не 2 ч; кроме того, в окне поровну.
    protons = sep((0, 1, "high"), (1, 24, "none"))
    meteors = sep((0, 1, "high"), (1, 24, "none")).model_copy(update={"line": "meteor_showers"})
    scores = [score(window(0, 6), [protons, meteors], RULES), score(window(6, 12), [protons, meteors], RULES)]
    rec = Recommendation(preferred=window(0, 6), reason="r", equivalents=(), insufficient=False, rules_applied=())
    text = explain(scores, rec, scores[0])
    assert text[1] == "Опасные минуты в окне, по каждой линии отдельно:"
    assert text[2].startswith("Метеоры: 1 ч.") and text[3].startswith("Протоны: 1 ч.")
    assert not any("2 ч" in s or "120" in s for s in text)


def test_zero_dangerous_minutes_from_a_forecast_is_not_said_as_measured() -> None:
    # Разбор приёмки 8 июня 2024 с отсечением 00:30 (D84): поток протонов не измерен все 1440 мин периода, уровень из
    # внешнего прогноза. Ноль опасных минут звучит как счёт по прогнозу и стоит после фразы о неизмеренном потоке.
    unmeasured = "Kp: прогноз Kp; поток: внешний прогноз вероятности 1 %"
    line = sep((0, 24, "none"))
    line = line.model_copy(
        update={
            "intervals": tuple(iv.model_copy(update={"note": unmeasured}) for iv in line.intervals),
            "metrics": {"unmeasured_minutes": 1440.0},
        }
    )
    scores = [score(w, [line], RULES) for w in (window(0, 6), window(6, 12))]
    rec = recommend(scores, RULES, requested=scores[1])
    text = with_notes(rec, window(6, 12), window(0, 24), line, [line], []).explanation
    assert text[1].startswith("Поток протонов не измерен на 24 ч из 24 ч периода поиска.")
    assert text[2] == "Опасных минут в окне нет, но это по прогнозу, измерений потока протонов нет."
    # Измеренный поток оговорки не получает.
    measured = with_notes(rec, window(6, 12), window(0, 24), sep((0, 24, "none")), [line], []).explanation
    assert "Опасных минут в окне нет." in measured
