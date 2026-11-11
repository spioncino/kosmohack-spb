from conftest import hours, interval, line, make_result, moment, window_score
from evarisk.api.schemas import AssessmentResult
from evarisk.briefing.facts import build_facts
from evarisk.briefing.llm import Message
from evarisk.briefing.service import build_briefing
from evarisk.briefing.templates import CLOSING
from evarisk.briefing.validate import LONGER_THAN_PERIOD, LONGER_THAN_WINDOW, MAX_WORDS, check_durations, validate
from evarisk.types import Recommendation, Window

CLEAN = (
    "Рекомендуем перенести начало на 08:00 UTC вместо 04:00. Уровень high и critical в запрошенном окне 60 мин, "
    f"в рекомендованном 0. По данным на 2024-05-10 12:30. {CLOSING}"
)
INVENTED_TIME = (
    "Рекомендуем перенести начало на 09:15 UTC вместо 04:00. Уровень high и critical в запрошенном окне 60 мин. "
    f"По данным на 2024-05-10 12:30. {CLOSING}"
)
INVENTED_NUMBER = (
    "Рекомендуем перенести начало на 08:00 UTC вместо 04:00. Вероятность события 55 %, уровень high 60 мин. "
    f"По данным на 2024-05-10 12:30. {CLOSING}"
)
NO_VERDICT = f"Обстановка спокойная, окно 04:00 UTC. По данным на 2024-05-10 12:30. {CLOSING}"
NO_BASIS = f"Рекомендуем перенести начало на 08:00 UTC вместо 04:00. Уровень high и critical 60 мин. {CLOSING}"
TOO_LONG = "Перенос на 08:00 UTC. " + "слово " * 70 + f"По данным на 2024-05-10. {CLOSING}"
NO_CLOSING_TEXT = "Рекомендуем перенести начало на 08:00 UTC вместо 04:00. По данным на 2024-05-10 12:30."


class FakeLlm:
    """Подменённая модель: отдаёт заранее заданные ответы и считает вызовы; сеть не трогается."""

    model = "fake-model"

    def __init__(self, *answers: str | Exception) -> None:
        self.answers = list(answers)
        self.calls: list[list[Message]] = []

    def complete(self, messages: list[Message]) -> str:
        self.calls.append(messages)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def move_to_result() -> AssessmentResult:
    """Перенос с 04:00 на 08:00: в запрошенном окне час уровня high, в рекомендованном ни минуты."""
    requested = window_score(moment(4), moment(10), hours(none=5.0, high=1.0))
    better = window_score(moment(8), moment(14), hours(none=6.0))
    protons = line(intervals=(interval(moment(4, 39), moment(5, 39), "high"),))
    return make_result(
        lines=(protons,),
        requested=requested,
        windows=(requested, better),
        recommendation=Recommendation(
            preferred=Window(start=moment(8), end=moment(14)),
            reason="причина не разбирается",
            equivalents=(),
            insufficient=False,
            rules_applied=(),
        ),
    )


def facts_of(result: AssessmentResult | None = None) -> object:
    return build_facts(result or move_to_result(), "summary")


def test_clean_text_of_the_model_is_taken_as_is() -> None:
    llm = FakeLlm(CLEAN)
    briefing = build_briefing(move_to_result(), "summary", llm=llm)
    assert briefing.source == "llm"
    assert briefing.model == "fake-model"
    assert briefing.validated
    assert briefing.violations == ()
    assert briefing.text == CLEAN
    assert len(llm.calls) == 1


def test_invented_time_is_rewritten_once_and_the_second_answer_is_taken() -> None:
    llm = FakeLlm(INVENTED_TIME, CLEAN)
    briefing = build_briefing(move_to_result(), "summary", llm=llm)
    assert briefing.source == "llm"
    assert briefing.text == CLEAN
    assert briefing.violations == ()
    assert len(llm.calls) == 2
    retry = llm.calls[1][-1]["content"]
    assert "09:15" in retry


def test_invented_number_twice_falls_back_to_the_template_with_the_violations() -> None:
    llm = FakeLlm(INVENTED_NUMBER, INVENTED_NUMBER)
    briefing = build_briefing(move_to_result(), "summary", llm=llm)
    assert briefing.source == "template"
    assert briefing.model == "fake-model"
    assert briefing.validated
    assert any("55" in v for v in briefing.violations)
    assert len(llm.calls) == 2


def test_missing_verdict_token_and_missing_basis_are_violations() -> None:
    facts = facts_of()
    assert any("08:00" in v for v in validate(NO_VERDICT, facts))
    assert any("по данным на" in v for v in validate(NO_BASIS, facts))


def test_too_long_text_and_missing_closing_sentence_are_violations() -> None:
    facts = facts_of()
    assert any(str(MAX_WORDS) in v for v in validate(TOO_LONG, facts))
    assert any(CLOSING in v for v in validate(NO_CLOSING_TEXT, facts))
    assert validate("", facts) == ["текст пуст"]


def test_failure_of_the_model_answers_with_the_template_and_names_the_reason() -> None:
    llm = FakeLlm(TimeoutError("превышено время ожидания"))
    briefing = build_briefing(move_to_result(), "summary", llm=llm)
    assert briefing.source == "template"
    assert briefing.validated
    assert briefing.violations and "TimeoutError" in briefing.violations[0]
    assert len(llm.calls) == 1
    assert briefing.text.endswith(CLOSING)


def test_without_the_model_the_answer_is_the_template_without_violations() -> None:
    briefing = build_briefing(move_to_result(), "summary")
    assert briefing.source == "template"
    assert briefing.model is None
    assert briefing.violations == ()
    assert briefing.validated
    assert briefing.audio_url is None


def test_dates_in_words_are_checked_against_the_facts() -> None:
    facts = facts_of()
    assert validate(CLEAN.replace("2024-05-10 12:30", "10 мая 12:30"), facts) == []
    assert any("12 мая" in v for v in validate(CLEAN.replace("2024-05-10 12:30", "12 мая 12:30"), facts))


def test_low_confidence_must_be_spoken_and_the_template_says_it() -> None:
    # Оценка протонов по внешнему прогнозу без измеренного потока имеет уверенность low: экипаж обязан это услышать.
    requested = window_score(moment(4), moment(10), hours(none=5.0, high=1.0))
    better = window_score(moment(8), moment(14), hours(none=6.0))
    protons = line(intervals=(interval(moment(4, 39), moment(5, 39), "high"),), grade="low")
    result = make_result(
        lines=(protons,),
        requested=requested,
        windows=(requested, better),
        recommendation=Recommendation(
            preferred=Window(start=moment(8), end=moment(14)),
            reason="причина не разбирается",
            equivalents=(),
            insufficient=False,
            rules_applied=(),
        ),
    )
    facts = build_facts(result, "summary")
    assert any("уверенность оценки протонов низкая" in v for v in validate(CLEAN, facts))
    spoken = CLEAN.replace(CLOSING, f"Уверенность оценки протонов низкая: поток не измерен. {CLOSING}")
    assert validate(spoken, facts) == []
    briefing = build_briefing(result, "summary", language="ru", llm=None)
    assert "Уверенность в оценке протонов низкая." in briefing.text and not briefing.violations


def test_verdict_must_be_the_first_sentence_and_the_text_does_not_order() -> None:
    # Настоящий ответ модели 19 сентября при вердикте «оснований нет»: «Оставить окно 04:00 UTC. Оснований для выбора
    # нет.» Проверка всего текста находила слова о нехватке оснований и пропускала противоречие (D77).
    facts = facts_of()
    late = CLEAN.replace("Рекомендуем перенести начало на 08:00 UTC вместо 04:00.", "Обстановка сложная. Начало 08:00.")
    assert any("в первой фразе нет времени начала рекомендованного окна 08:00" in v for v in validate(late, facts))
    for word, text in (
        ("перенесите", CLEAN.replace("Рекомендуем перенести", "Перенесите")),
        ("переносим", CLEAN.replace("Рекомендуем перенести", "Переносим")),
        ("не опасн", CLEAN.replace(CLOSING, f"Протоны не опасны. {CLOSING}")),
        ("безопас", CLEAN.replace(CLOSING, f"Выход безопасен. {CLOSING}")),
    ):
        assert any(f"«{word}»" in v for v in validate(text, facts)), word
    # «окне опасных» не запрещённое «не опасн»: проверка идёт по началу слова.
    assert validate(CLEAN.replace(CLOSING, f"В новом окне опасных минут нет. {CLOSING}"), facts) == []


def test_lines_of_the_model_are_joined_into_one_paragraph() -> None:
    llm = FakeLlm(CLEAN.replace(". ", ".\n"))
    briefing = build_briefing(move_to_result(), "summary", llm=llm)
    assert briefing.source == "llm" and briefing.text == CLEAN


def elevated_move() -> AssessmentResult:
    """Разбор 10 мая: перенос с 13:00 на 10:45, повышенного фона 282 мин в запрошенном окне и 147 мин в новом."""
    requested = window_score(moment(13, day=10), moment(19, day=10), hours(none=1.3, elevated=4.7))
    preferred = window_score(moment(10, 45, day=10), moment(16, 45, day=10), hours(none=3.55, elevated=2.45))
    return make_result(
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


def test_durations_are_checked_whole_against_the_facts() -> None:
    # Длительности «4 ч 42 мин» посчитаны в фактах (D78), модель их не считает. Длительность, которой в фактах нет,
    # отвергается целиком, а её части «4» и «42» разрешёнными числами не становятся.
    facts = build_facts(elevated_move(), "summary")
    text = (
        "Рекомендуем начать выход на 2 ч 15 мин раньше, в 10:45 UTC. Повышенного фона в новом окне 2 ч 27 мин, "
        f"а в запрошенном 4 ч 42 мин. Окно 6 ч. Это разбор по архиву, а не прогноз. {CLOSING}"
    )
    assert validate(text, facts) == []
    assert validate(text.replace("4 ч 42 мин", "4 часа 42 минуты"), facts) == []
    assert any("длительность 4 ч 41 мин" in v for v in validate(text.replace("4 ч 42 мин", "4 ч 41 мин"), facts))
    assert any("длительность 3 ч" in v for v in validate(text.replace("Окно 6 ч.", "Сдвиг 3 ч."), facts))
    assert any("число 42" in v for v in validate(text.replace("Окно 6 ч.", "Минут 42."), facts))


def test_duration_longer_than_the_window_is_rejected_even_when_allowed() -> None:
    # D84: окно 6 ч, период поиска 00:00–14:00. Длительность о минутах окна не длиннее окна, даже если её число
    # оказалось среди разрешённых; сдвиг начала и неизмеренный поток за период ограничены периодом поиска.
    facts = build_facts(move_to_result(), "summary")
    facts = facts.model_copy(update={"allowed_values": (*facts.allowed_values, "12 ч", "8 ч", "400")})
    summed = (
        "Рекомендуем начать в 08:00 UTC. Повышенного фона в запрошенном окне 12 ч. "
        f"По данным на 2024-05-10 12:30. {CLOSING}"
    )
    assert check_durations(summed, facts) == [LONGER_THAN_WINDOW.format(value="12 ч", limit="6 ч")]
    assert LONGER_THAN_WINDOW.format(value="12 ч", limit="6 ч") in validate(summed, facts)
    assert check_durations("Без данных по протонам 400 мин.", facts) == [
        LONGER_THAN_WINDOW.format(value="400 мин", limit="6 ч")
    ]
    assert check_durations("Лучше начать на 8 ч позже. Поток протонов не измерен за период 12 ч.", facts) == []
    assert check_durations("Лучше начать на 15 ч позже.", facts) == [
        LONGER_THAN_PERIOD.format(value="15 ч", limit="14 ч")
    ]
    assert check_durations("Повышенного фона по протонам 6 ч, по метеорным потокам 45 мин.", facts) == []
