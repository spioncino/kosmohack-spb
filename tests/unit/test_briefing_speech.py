import pytest

from conftest import hours, interval, line, make_result, moment, window_score
from evarisk.briefing.service import SPEECH_FROM_TEMPLATE, build_briefing
from evarisk.briefing.speech import (
    SpeechNotSpeakable,
    day_words,
    decimal_words,
    number_words,
    time_words,
    to_speech,
)
from evarisk.briefing.templates import CLOSING
from evarisk.types import Recommendation, Window

# Синтезатор Silero v4_ru пропускает цифры и латиницу молча, поэтому в речевом тексте их быть не должно.
LATIN_OR_DIGIT = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


@pytest.mark.parametrize(
    ("hours_value", "minutes_value", "expected"),
    [
        (4, 39, "четыре тридцать девять"),
        (8, 0, "восемь ноль-ноль"),
        (14, 5, "четырнадцать ноль пять"),
        (0, 0, "ноль ноль-ноль"),
        (23, 59, "двадцать три пятьдесят девять"),
    ],
)
def test_time_is_read_as_hour_and_minutes(hours_value: int, minutes_value: int, expected: str) -> None:
    assert time_words(hours_value, minutes_value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "ноль"),
        (1, "один"),
        (11, "одиннадцать"),
        (40, "сорок"),
        (145, "сто сорок пять"),
        (1440, "одна тысяча четыреста сорок"),
    ],
)
def test_integers_are_spelled_out(value: int, expected: str) -> None:
    assert number_words(value) == expected


def test_gender_of_the_numeral_follows_the_unit() -> None:
    assert number_words(1, "f") == "одна"
    assert number_words(22, "f") == "двадцать две"
    assert number_words(1, "n") == "одно"
    assert number_words(2, "n") == "два"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 мин", "одна минута"),
        ("2 мин", "две минуты"),
        ("18 мин", "восемнадцать минут"),
        ("22 мин", "двадцать две минуты"),
        ("40 мин", "сорок минут"),
        ("1 ч", "один час"),
        ("2 ч", "два часа"),
        ("6 ч", "шесть часов"),
        ("55 %", "пятьдесят пять процентов"),
        ("1 окно", "одно окно"),
        ("2 окна", "два окна"),
        ("13 окон", "тринадцать окон"),
    ],
)
def test_units_agree_with_the_numeral(text: str, expected: str) -> None:
    assert to_speech(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("0.92°", "ноль целых девяносто две сотых градуса"), ("2.5 ч", "две целых пять десятых часа")],
)
def test_decimals_are_read_as_fractions(text: str, expected: str) -> None:
    assert to_speech(text) == expected
    assert decimal_words("0", "92") == "ноль целых девяносто две сотых"


@pytest.mark.parametrize(
    ("day", "expected"),
    [(1, "первого"), (11, "одиннадцатого"), (21, "двадцать первого"), (30, "тридцатого"), (31, "тридцать первого")],
)
def test_day_of_the_date_is_ordinal(day: int, expected: str) -> None:
    assert day_words(day) == expected


def test_date_loses_the_year_and_keeps_the_month() -> None:
    # После предлога «на» дата в винительном падеже.
    assert to_speech("По данным на 2024-05-11.") == "По данным на одиннадцатое мая."


def test_window_and_utc_are_read_in_words() -> None:
    assert (
        to_speech("Окно 08:00–14:00 UTC.") == "Окно восемь ноль-ноль до четырнадцать ноль-ноль по всемирному времени."
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("sep_protons high", "протоны высокий"),
        ("meteor_showers elevated", "метеорные потоки повышенный"),
        ("conjunctions critical", "сближения критический"),
        ("Kp по прогнозу", "ка-пэ индекс по прогнозу"),
        ("Kp-поправка", "ка-пэ поправка"),
        ("DONKI и SWPC и GOES", "донки и центр космической погоды и гоуз"),
    ],
)
def test_codes_and_levels_become_russian_words(text: str, expected: str) -> None:
    assert to_speech(text) == expected


def test_clause_with_untranslated_latin_is_dropped_whole() -> None:
    assert to_speech("Перенос, по данным ABC monitor, подтверждён.") == "Перенос, подтверждён."


def test_speech_of_the_template_has_neither_digits_nor_latin() -> None:
    requested = window_score(moment(4), moment(10), hours(none=5.0, high=1.0))
    protons = line(intervals=(interval(moment(4, 39), moment(5, 39), "high"),))
    result = make_result(lines=(protons,), requested=requested, windows=(requested,))
    briefing = build_briefing(result, "summary")
    assert briefing.speech_text
    assert not set(briefing.speech_text) & set(LATIN_OR_DIGIT)
    assert briefing.speech_text.endswith(CLOSING)


def test_unspeakable_text_raises_and_briefing_falls_back_to_the_template_speech() -> None:
    with pytest.raises(SpeechNotSpeakable):
        to_speech("Остаток 5я.")
    with pytest.raises(SpeechNotSpeakable):
        to_speech("latin only")


def test_briefing_takes_the_template_speech_when_the_text_of_the_model_is_unspeakable() -> None:
    requested = window_score(moment(4), moment(10), hours(none=5.0, high=1.0))
    better = window_score(moment(8), moment(14), hours(none=6.0))
    result = make_result(
        requested=requested,
        windows=(requested, better),
        recommendation=Recommendation(
            preferred=Window(start=moment(8), end=moment(14)),
            reason="причина",
            equivalents=(),
            insufficient=False,
            rules_applied=(),
        ),
    )
    text = (
        f"Рекомендуем перенести начало на 08:00 UTC вместо 04:00. Уровень 0я. По данным на 2024-05-10 12:30. {CLOSING}"
    )

    class Unspeakable:
        model = "fake-model"

        def complete(self, messages: list[dict[str, str]]) -> str:
            return text

    briefing = build_briefing(result, "summary", llm=Unspeakable())
    assert briefing.text == text
    assert briefing.source == "llm"
    assert SPEECH_FROM_TEMPLATE in briefing.violations
    assert not set(briefing.speech_text) & set(LATIN_OR_DIGIT)


def test_date_after_the_preposition_na_is_accusative() -> None:
    # «По данным на 2024-05-10» читается «на десятое мая», а не «на десятого мая»; без предлога остаётся родительный.
    assert "на десятое мая" in to_speech("По данным на 2024-05-10 12:30 UTC.")
    assert "на двадцать третье июня" in to_speech("По данным на 2024-06-23 00:30 UTC.")
    assert "на третье мая" in to_speech("По данным на 2024-05-03 00:30 UTC.")
    assert "от десятого мая" in to_speech("Выпуск от 2024-05-10.")
