import re

from evarisk.briefing.facts import hm
from evarisk.briefing.models import BriefingFacts
from evarisk.briefing.templates import CLOSING, MAX_WORDS, words
from evarisk.windows.explain import duration_text

# Предел длины ответа на свой вопрос (D89): прямой ответ, до трёх фраз по фактам, основание данных и закрытие.
ASK_MAX_WORDS = 90
INSUFFICIENT_MARKERS = (
    "недостаточно",
    "нет оснований",
    "оснований нет",
    "оснований для выбора нет",
    "оснований для выбора окна нет",
)
EMPTY = "текст пуст"
NO_CLOSING = f"текст не заканчивается фразой «{CLOSING}»"
TOO_LONG = "в тексте {count} слов, допустимо не больше {limit}"
NO_START = "в первой фразе нет времени начала рекомендованного окна {value}"
NO_REQUESTED = "в первой фразе нет времени начала запрошенного окна {value}"
NO_INSUFFICIENT = "в первой фразе нет слов о нехватке оснований (недостаточно, оснований нет)"
# Решение и оценку безопасности выносит руководитель работ (D77): модель не приказывает и не называет обстановку
# безопасной; «опасная часть до экипажа не доходит» не равно «протоны не опасны».
FORBIDDEN_WORDS = ("перенесите", "переносим", "выходите", "безопас", "не опасн")
_FORBIDDEN = [(word, re.compile(rf"(?<!\w){word}")) for word in FORBIDDEN_WORDS]
FORBIDDEN = "в тексте «{word}»: сообщение не приказывает и не оценивает безопасность, решение за руководителем работ"
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
NO_CUTOFF_WORDS = "в тексте нет слов «по данным на»: прогноз из прошлого без момента отсечения неотличим от текущего"
NO_LOW_CONFIDENCE = "в тексте не сказано, что уверенность оценки протонов низкая"
NO_REVIEW_WORDS = "в тексте нет слов о разборе по архиву: разбор не выдаётся за прогноз"
REPLAY_WORDS = "по данным на"
REVIEW_WORDS = ("разбор", "архив")
UNKNOWN_TIME = "время {value} в фактах не встречается"
UNKNOWN_DATE = "дата {value} в фактах не встречается"
UNKNOWN_NUMBER = "число {value} в фактах не встречается"
UNKNOWN_DURATION = "длительность {value} в фактах не встречается"
# Смысловая проверка длительностей (D84): минуты разных линий идут одновременно и не складываются, поэтому ни одна
# длительность о минутах окна не длиннее самого окна. «12 ч» повышенного фона в окне 6 ч это сумма двух линий.
LONGER_THAN_WINDOW = (
    "длительность {value} длиннее окна {limit}: минуты разных видов опасности не складываются, называй их по каждому "
    "виду отдельно"
)
LONGER_THAN_PERIOD = "длительность {value} длиннее периода поиска {limit}"
PERIOD_WORD = "период"
# Поток протонов в окне не измерен (D64, D84): текст обязан сказать, что опасные минуты посчитаны по прогнозу.
NO_FORECAST_WORDS = (
    "в тексте нет слова «прогноз»: поток протонов в окне не измерен, ноль опасных минут это счёт по прогнозу"
)
FORECAST_WORD = "прогноз"

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TIME = re.compile(r"\d{1,2}:\d{2}")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
# Длительность «4 ч 42 мин», «6 ч», «2 часа 15 минут» сверяется целиком (D78): часы с минутами это одно значение
# факта, и «4» с «42» по отдельности разрешёнными числами не становятся.
_DURATION = re.compile(
    r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*(?:ч|час|часа|часов)(?![а-яё])"
    r"(?:\s+(\d+)\s*(?:мин|минута|минуты|минут)(?![а-яё]))?",
    re.IGNORECASE,
)
# Длительность одними минутами «45 мин»: `check_values` сверяет её как число, длину сверяет `check_durations` (D84).
_MINUTES_ONLY = re.compile(r"(?<![\d.,:])(\d+)\s*(?:мин|минута|минуты|минут)(?![а-яё])", re.IGNORECASE)
# Сдвиг начала окна: длительность сразу после «на» или сразу перед «раньше», «позже» («на 5 ч раньше»).
_SHIFT_BEFORE = re.compile(r"(?<![а-яё])на\s+$", re.IGNORECASE)
_SHIFT_AFTER = re.compile(r"\s*(?:раньше|позже)(?![а-яё])", re.IGNORECASE)
_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
_RU_DATE = re.compile(r"\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\b")


def _time_key(value: str) -> tuple[int, int]:
    hours, minutes = value.split(":")
    return int(hours), int(minutes)


def _duration_minutes(match: re.Match[str]) -> int:
    return round(float(match.group(1).replace(",", ".")) * 60.0) + int(match.group(2) or 0)


def _day_key(value: str) -> tuple[int, int]:
    parts = value.split("-")
    return int(parts[1]), int(parts[2])


class Allowed:
    """Разрешённые значения фактов в виде, удобном для сравнения: время без ведущего нуля, число как число."""

    def __init__(self, values: tuple[str, ...]) -> None:
        self.times: set[tuple[int, int]] = set()
        self.dates: set[str] = set()
        self.days: set[tuple[int, int]] = set()
        self.numbers: set[float] = set()
        self.durations: set[int] = set()
        for value in values:
            if _DATE.fullmatch(value):
                self.dates.add(value)
                self.days.add(_day_key(value))
            elif _TIME.fullmatch(value):
                self.times.add(_time_key(value))
            elif match := _DURATION.fullmatch(value):
                self.durations.add(_duration_minutes(match))
            else:
                self.numbers.add(float(value.replace(",", ".")))


def check_values(text: str, allowed: Allowed) -> list[str]:
    """Каждая дата, время, длительность и число текста сверяются со списком фактов; даты словами тоже."""
    found: list[str] = []
    rest = text
    for value in _DATE.findall(rest):
        if value not in allowed.dates:
            found.append(UNKNOWN_DATE.format(value=value))
    rest = _DATE.sub(" ", rest)
    for day, month in _RU_DATE.findall(rest):
        if (_MONTHS[month], int(day)) not in allowed.days:
            found.append(UNKNOWN_DATE.format(value=f"{day} {month}"))
    rest = _RU_DATE.sub(" ", rest)
    for value in _TIME.findall(rest):
        if _time_key(value) not in allowed.times:
            found.append(UNKNOWN_TIME.format(value=value))
    rest = _TIME.sub(" ", rest)
    for match in _DURATION.finditer(rest):
        if _duration_minutes(match) not in allowed.durations:
            found.append(UNKNOWN_DURATION.format(value=match.group(0)))
    rest = _DURATION.sub(" ", rest)
    for value in _NUMBER.findall(rest):
        if float(value.replace(",", ".")) not in allowed.numbers:
            found.append(UNKNOWN_NUMBER.format(value=value))
    return list(dict.fromkeys(found))


def value_tokens(text: str) -> list[str]:
    """Даты, времена, длительности и числа строки фактов в виде, который понимает `Allowed` (D89).

    Длительность «1 ч 12 мин» остаётся одним значением, как в проверке текста; «45 мин» даёт число 45.
    """
    tokens = _DATE.findall(text)
    rest = _DATE.sub(" ", text)
    tokens += _TIME.findall(rest)
    rest = _TIME.sub(" ", rest)
    minutes = [_duration_minutes(m) for m in _DURATION.finditer(rest)]
    tokens += [duration_text(value) if value >= 60 else str(value) for value in minutes]
    rest = _DURATION.sub(" ", rest)
    tokens += _NUMBER.findall(rest)
    return list(dict.fromkeys(tokens))


def _stated_durations(sentence: str) -> list[tuple[str, int, int, int]]:
    """Длительности фразы: текст, минуты, начало и конец; «4 ч 42 мин» одна длительность, «45 мин» тоже."""
    found = [(m.group(0), _duration_minutes(m), m.start(), m.end()) for m in _DURATION.finditer(sentence)]
    # Часы с минутами заменяются пробелами той же длины: позиции остальных длительностей не сдвигаются.
    rest = _DURATION.sub(lambda m: " " * len(m.group(0)), sentence)
    found += [(m.group(0), int(m.group(1)), m.start(), m.end()) for m in _MINUTES_ONLY.finditer(rest)]
    return found


def check_durations(text: str, facts: BriefingFacts) -> list[str]:
    """Длительность в тексте не длиннее самой длинной длительности фактов, к которой она может относиться (D84).

    Правило: любая длительность не длиннее самого длинного окна фактов. Исключений два, и они ограничены периодом
    поиска: сдвиг начала окна (сразу после «на» или перед «раньше», «позже») и длительность во фразе со словом
    «период» (неизмеренный поток протонов за период). Минуты окна по любой линии не длиннее окна, поэтому «12 ч»
    повышенного фона в окне 6 ч отклоняется, даже если число совпало с разрешённым значением.
    """
    windows = [facts.requested] if facts.recommended is None else [facts.requested, facts.recommended]
    window = round(max(w.duration_hours for w in windows) * 60.0)
    period = facts.search_minutes
    found: list[str] = []
    for sentence in _SENTENCE_END.split(text.strip()):
        about_period = PERIOD_WORD in sentence.lower()
        for value, minutes, start, end in _stated_durations(sentence):
            shift = bool(_SHIFT_BEFORE.search(sentence[:start])) or bool(_SHIFT_AFTER.match(sentence, end))
            if shift or about_period:
                if period is not None and minutes > period:
                    found.append(LONGER_THAN_PERIOD.format(value=value.strip(), limit=duration_text(round(period))))
            elif minutes > window:
                found.append(LONGER_THAN_WINDOW.format(value=value.strip(), limit=duration_text(window)))
    return list(dict.fromkeys(found))


def first_sentence(text: str) -> str:
    return _SENTENCE_END.split(text.strip(), maxsplit=1)[0]


def _verdict_violation(text: str, facts: BriefingFacts) -> str | None:
    """Вердикт звучит первой фразой (D77): «Оставить окно 04:00. Оснований для выбора нет.» противоречит себе."""
    first = first_sentence(text)
    if facts.verdict == "insufficient":
        return None if any(marker in first.lower() for marker in INSUFFICIENT_MARKERS) else NO_INSUFFICIENT
    times = {_time_key(t) for t in _TIME.findall(first)}
    if facts.verdict == "move_to" and facts.recommended is not None:
        value = hm(facts.recommended.start)
        return None if _time_key(value) in times else NO_START.format(value=value)
    value = hm(facts.requested.start)
    return None if _time_key(value) in times else NO_REQUESTED.format(value=value)


def _basis_violation(text: str, facts: BriefingFacts) -> str | None:
    """Основание данных обязано звучать в тексте: прогноз из прошлого и разбор не выдаются за обстановку сейчас."""
    lowered = text.lower()
    if facts.mode == "replay":
        return None if REPLAY_WORDS in lowered else NO_CUTOFF_WORDS
    if facts.mode == "review":
        return None if any(word in lowered for word in REVIEW_WORDS) else NO_REVIEW_WORDS
    return None


def validate(text: str, facts: BriefingFacts, *, ask: bool = False) -> list[str]:
    """Нарушения текста относительно фактов; пустой список это «числа проверены, текст можно отдавать».

    `ask` это ответ на свой вопрос экипажа (D89): первая фраза отвечает на вопрос, поэтому вердикт в ней не
    требуется, а предел длины `ASK_MAX_WORDS`. Числа, длительности, запреты, основание данных, низкая уверенность
    и последняя фраза проверяются так же.
    """
    if not text.strip():
        return [EMPTY]
    violations: list[str] = []
    if not text.strip().endswith(CLOSING):
        violations.append(NO_CLOSING)
    count, limit = words(text), ASK_MAX_WORDS if ask else MAX_WORDS
    if count > limit:
        violations.append(TOO_LONG.format(count=count, limit=limit))
    verdict = None if ask else _verdict_violation(text, facts)
    if verdict is not None:
        violations.append(verdict)
    basis = _basis_violation(text, facts)
    if basis is not None:
        violations.append(basis)
    lowered = text.lower()
    if facts.confidence_grade == "low" and not ("уверенност" in lowered and "низк" in lowered):
        violations.append(NO_LOW_CONFIDENCE)
    violations.extend(FORBIDDEN.format(word=word) for word, pattern in _FORBIDDEN if pattern.search(lowered))
    violations.extend(check_values(text, Allowed(facts.allowed_values)))
    violations.extend(check_durations(text, facts))
    windows = (facts.requested, facts.recommended)
    if any(w is not None and w.flux_unmeasured_minutes for w in windows) and FORECAST_WORD not in lowered:
        violations.append(NO_FORECAST_WORDS)
    return violations
