"""Свой вопрос экипажа (D89): факты вопроса, сдвиги из вопроса, проверка ответа и честный отказ."""

from datetime import timedelta
from pathlib import Path

from conftest import interval, line
from evarisk.api.schemas import AssessmentResult
from evarisk.briefing.ask import ask_context, shifts, targets
from evarisk.briefing.llm import ASK_HISTORY, ASK_SYSTEM, ask_messages
from evarisk.briefing.service import NOT_ANSWERED, answer_question, clean_history
from evarisk.briefing.templates import CLOSING
from evarisk.briefing.validate import NO_START, TOO_LONG, validate

FIXTURE = Path("tests/fixtures/assessments/review_2024-05-10.json")
# Разбор 10 мая 2024: запрошено 14:00–20:00, рекомендовано 13:00–19:00, окна с 13:00 до 16:00 через 15 мин,
# у протонов отрезок high 17:18–17:58.
BASIS = "Это разбор по архиву, а не прогноз."


def stored() -> AssessmentResult:
    return AssessmentResult.model_validate_json(FIXTURE.read_text(encoding="utf-8"))


class Scripted:
    """Модель с заранее заданными ответами по очереди."""

    model = "fake-model"

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.seen: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.seen.append(messages)
        return self.answers.pop(0)


def test_relative_shifts_read_direction_and_amount() -> None:
    hour, half = timedelta(hours=1), timedelta(minutes=30)
    assert shifts("что будет, если задержимся на час?") == [hour]
    assert shifts("а если на 30 минут раньше?") == [-half]
    assert shifts("можно на полчаса позже?") == [half]
    assert shifts("а на 2 часа раньше") == [-2 * hour]
    assert shifts("на полтора часа позже") == [timedelta(minutes=90)]
    # Без направления сдвиг даётся в обе стороны.
    assert shifts("а если сдвинуть на час?") == [hour, -hour]
    assert shifts("почему именно это окно?") == []


def test_question_times_snap_to_the_grid_of_windows() -> None:
    found = targets(stored(), "а если выйти в 15:07?")
    assert found[0].label == "в вопросе названо 15:07"
    assert (found[0].start.hour, found[0].start.minute) == (15, 7)
    context = ask_context(stored(), "а если выйти в 15:07?")
    first = next(text for text in context.lines if text.startswith("в вопросе названо 15:07"))
    assert "окно с 15:00 до 21:00 UTC" in first


def test_facts_for_a_named_time_hold_that_window_and_its_numbers() -> None:
    context = ask_context(stored(), "а если выйти в 15:00?")
    line_of = next(text for text in context.lines if text.startswith("в вопросе названо 15:00"))
    # Окно 15:00–21:00 накрывает отрезок high 17:18–17:58: 40 опасных минут по протонам.
    assert "окно с 15:00 до 21:00 UTC" in line_of and "опасных минут по протонам 40" in line_of
    assert "окно не исключено" in line_of
    for value in ("15:00", "21:00", "40"):
        assert value in context.facts.allowed_values
    # Всегда есть окна в пределах часа от запрошенного и рекомендованного начала; окна 12:00 в периоде нет.
    plain = ask_context(stored(), "почему?").lines
    assert any(text.startswith("на 1 ч позже запрошенного: окно с 15:00 до 21:00 UTC") for text in plain)
    assert "на 1 ч раньше рекомендованного: окна с началом в 12:00 UTC в периоде поиска нет." in plain
    # Объяснение выбора окна у расчёта до D76 собрано из тех же оценок окон.
    assert context.lines[1].startswith("Почему выбрано это окно, объяснение расчёта: ")
    assert "Опасные отрезки в запрошенном окне: протоны достают станцию с 17:18 до 17:58." in context.lines
    assert "Тень в запрошенном окне: данных об освещённости нет." in context.lines


def test_shadow_in_a_window_comes_from_the_illumination_line() -> None:
    base = stored()
    day = base.requested_window.window.start
    shadow = line(
        "illumination",
        (
            interval(day, day + timedelta(minutes=40), "none"),
            interval(day + timedelta(minutes=40), day + timedelta(minutes=76), "elevated"),
            interval(day + timedelta(minutes=76), day + timedelta(hours=8), "none"),
        ),
    )
    result = base.model_copy(update={"lines": [*base.lines, shadow]})
    context = ask_context(result, "когда будет тень?")
    assert "Тень в запрошенном окне с 14:40 до 15:16, отрезков тени 1, в тени всего 36 мин" in " ".join(context.lines)
    assert "14:40" in context.facts.allowed_values and "36" in context.facts.allowed_values


def test_ask_mode_checks_numbers_but_not_the_verdict_first() -> None:
    facts = ask_context(stored(), "когда опасно?").facts
    text = f"Протоны достают станцию с 17:18 до 17:58. {BASIS} {CLOSING}"
    assert NO_START.format(value="13:00") in validate(text, facts)
    assert validate(text, facts, ask=True) == []
    invented = f"Протоны достают станцию с 17:20 до 17:58. {BASIS} {CLOSING}"
    assert any("17:20" in v for v in validate(invented, facts, ask=True))
    no_basis = f"Протоны достают станцию с 17:18 до 17:58. {CLOSING}"
    assert validate(no_basis, facts, ask=True) != []
    unsafe = f"Окно с 13:00 безопасно. {BASIS} {CLOSING}"
    assert any("безопас" in v for v in validate(unsafe, facts, ask=True))
    # Сводке хватает 70 слов, ответу на вопрос до 90: 86 слов проходят, 101 нет.
    long = "Протоны достают станцию. " * 25 + f"{BASIS} {CLOSING}"
    assert validate(long, facts, ask=True) == []
    longer = "Протоны достают станцию. " * 30 + f"{BASIS} {CLOSING}"
    assert TOO_LONG.format(count=101, limit=90) in validate(longer, facts, ask=True)


def test_a_rejected_answer_says_first_that_the_question_is_not_answered() -> None:
    llm = Scripted("Выходите в 09:15.", "Выходите в 09:15.")
    briefing = answer_question(stored(), "а если выйти в 15:00?", llm=llm)
    assert briefing.source == "template"
    assert briefing.text.startswith(NOT_ANSWERED)
    assert "09:15" not in briefing.text and briefing.validated is True
    assert "не прошёл проверку" in briefing.violations[0]
    assert len(llm.seen) == 2


def test_without_a_model_the_summary_is_not_passed_off_as_an_answer() -> None:
    briefing = answer_question(stored(), "а если выйти в 15:00?")
    assert briefing.source == "template" and briefing.text.startswith(NOT_ANSWERED)


def test_an_answer_of_the_model_from_the_question_facts_is_shown() -> None:
    answer = f"В окне с 15:00 опасных минут по протонам 40. {BASIS} {CLOSING}"
    llm = Scripted(answer)
    briefing = answer_question(stored(), "а если выйти в 15:00?", llm=llm, history=[("когда лучше?", "В 13:00.")])
    assert briefing.source == "llm" and briefing.text == answer and briefing.validated is True
    system, user = llm.seen[0]
    assert system["content"] == ASK_SYSTEM
    assert "Образец сообщения" not in user["content"]
    assert "в вопросе названо 15:00, такое окно в расчёте есть: окно с 15:00 до 21:00 UTC" in user["content"]
    assert ASK_HISTORY in user["content"] and "Вопрос: «когда лучше?». Ответ: «В 13:00.»" in user["content"]


def test_history_keeps_the_last_three_turns_as_data() -> None:
    turns = clean_history([(f"вопрос {n}", f"ответ\n{n}") for n in range(5)] + [("", "пусто")])
    assert turns == (("вопрос 2", "ответ 2"), ("вопрос 3", "ответ 3"), ("вопрос 4", "ответ 4"))
    content = ask_messages(ask_context(stored(), "а дальше?").facts, "а дальше?", (), turns)[1]["content"]
    assert "вопрос 1" not in content and "Вопрос: «вопрос 4». Ответ: «ответ 4»" in content
