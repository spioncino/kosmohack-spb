import re

ONES = ("ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять")
GENDERED = {"f": {1: "одна", 2: "две"}, "n": {1: "одно"}}
TEENS = (
    "десять",
    "одиннадцать",
    "двенадцать",
    "тринадцать",
    "четырнадцать",
    "пятнадцать",
    "шестнадцать",
    "семнадцать",
    "восемнадцать",
    "девятнадцать",
)
TENS = ("", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто")
HUNDREDS = ("", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот")
MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
ORDINAL_DAY = {
    1: "первого",
    2: "второго",
    3: "третьего",
    4: "четвёртого",
    5: "пятого",
    6: "шестого",
    7: "седьмого",
    8: "восьмого",
    9: "девятого",
    10: "десятого",
    11: "одиннадцатого",
    12: "двенадцатого",
    13: "тринадцатого",
    14: "четырнадцатого",
    15: "пятнадцатого",
    16: "шестнадцатого",
    17: "семнадцатого",
    18: "восемнадцатого",
    19: "девятнадцатого",
    20: "двадцатого",
    30: "тридцатого",
}
FRACTIONS = {
    1: ("десятая", "десятых", "десятых"),
    2: ("сотая", "сотых", "сотых"),
    3: ("тысячная", "тысячных", "тысячных"),
}
# Единица: род числительного, формы для 1, 2–4 и 5–20, форма после дробного числа.
UNITS: dict[str, tuple[str, str, str, str, str]] = {
    "мин": ("f", "минута", "минуты", "минут", "минуты"),
    "минут": ("f", "минута", "минуты", "минут", "минуты"),
    "минута": ("f", "минута", "минуты", "минут", "минуты"),
    "минуты": ("f", "минута", "минуты", "минут", "минуты"),
    "ч": ("m", "час", "часа", "часов", "часа"),
    "%": ("m", "процент", "процента", "процентов", "процента"),
    "°": ("m", "градус", "градуса", "градусов", "градуса"),
    "окно": ("n", "окно", "окна", "окон", "окна"),
    "окна": ("n", "окно", "окна", "окон", "окна"),
    "окон": ("n", "окно", "окна", "окон", "окна"),
}
ZERO_MINUTES = "ноль-ноль"
UTC_SPOKEN = "по всемирному времени"
FALLBACK_SPEECH = "Сводка передана текстом на экран. Решение за руководителем работ."
TERMS = {
    "UTC": UTC_SPOKEN,
    "SEP": "протонное событие",
    "Kp": "ка-пэ индекс",
    "DONKI": "донки",
    "SWPC": "центр космической погоды",
    "GOES": "гоуз",
    "critical": "критический",
    "high": "высокий",
    "elevated": "повышенный",
    "none": "фон",
    "unknown": "без уровня",
    "sep_protons": "протоны",
    "meteor_showers": "метеорные потоки",
    "conjunctions": "сближения",
    "dose_proxy": "прокси дозы",
    "illumination": "освещённость",
    "missing": "данных нет",
    "stale": "данные устарели",
    "unusable_for_replay": "непригодно для прогноза из прошлого",
    "available": "данные есть",
}

_NUMBER = r"\d+(?:[.,]\d+)?"
_TIME = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_SPAN = re.compile(r"\b(\d{1,2}:\d{2})\s*[–—-]\s*(\d{1,2}:\d{2})\b")
_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_VALUE = re.compile(rf"({_NUMBER})\s*(" + "|".join(sorted(UNITS, key=len, reverse=True)) + r")?(?![0-9A-Za-zА-Яа-я])")
_KP_PREFIX = re.compile(r"\bKp-")
_TERMS = re.compile(r"(?<![A-Za-z])(" + "|".join(sorted(TERMS, key=len, reverse=True)) + r")(?![A-Za-z])")
_LATIN_OR_DIGIT = re.compile(r"[0-9A-Za-z]")
_LATIN = re.compile(r"[A-Za-z]")
_CLAUSES = re.compile(r"([.;,:!?]+\s*)")
_EMPTY_CLAUSE = re.compile(r"\s*,\s*(?=[.;:!?])")
_SPACES = re.compile(r"[ \t]{2,}")


class SpeechNotSpeakable(ValueError):  # noqa: N818
    """Текст не удалось перевести в произносимый вид: остались цифры или латиница."""


def plural(value: int, one: str, few: str, many: str) -> str:
    if 11 <= value % 100 <= 14:
        return many
    last = value % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def number_words(value: int, gender: str = "m") -> str:
    """Целое число словами до 9999: больше в сводке не встречается (период поиска не длиннее суток)."""
    if value >= 1000:
        thousands = value // 1000
        head = f"{number_words(thousands, 'f')} {plural(thousands, 'тысяча', 'тысячи', 'тысяч')}"
        tail = value % 1000
        return head if tail == 0 else f"{head} {number_words(tail, gender)}"
    parts: list[str] = []
    if value >= 100:
        parts.append(HUNDREDS[value // 100])
        value %= 100
    if 10 <= value <= 19:
        parts.append(TEENS[value - 10])
        return " ".join(parts)
    if value >= 20:
        parts.append(TENS[value // 10])
        value %= 10
    if value > 0 or not parts:
        parts.append(GENDERED.get(gender, {}).get(value) or ONES[value])
    return " ".join(parts)


def decimal_words(whole: str, fraction: str) -> str:
    """Дробь словами: «0.92» это «ноль целых девяносто две сотых»."""
    whole_value, fraction_value = int(whole), int(fraction)
    digits = min(len(fraction), max(FRACTIONS))
    forms = FRACTIONS[digits]
    return (
        f"{number_words(whole_value, 'f')} {plural(whole_value, 'целая', 'целых', 'целых')} "
        f"{number_words(fraction_value, 'f')} {plural(fraction_value, *forms)}"
    )


_ON_DAY = re.compile(r"(\b[Нн]а )((?:двадцать |тридцать )?[а-яё]+[ое]го) ")


def _accusative_day(words: str) -> str:
    """День из родительного падежа в винительный: «десятого» → «десятое», «третьего» → «третье»."""
    return f"{words[:-3]}е" if words.endswith("третьего") else f"{words[:-3]}ое"


def day_words(day: int) -> str:
    if day in ORDINAL_DAY:
        return ORDINAL_DAY[day]
    if 21 <= day <= 29:
        return f"двадцать {ORDINAL_DAY[day - 20]}"
    return f"тридцать {ORDINAL_DAY[day - 30]}"


def time_words(hours: int, minutes: int) -> str:
    """Время для чтения вслух: «04:39» это «четыре тридцать девять», «08:00» это «восемь ноль-ноль»."""
    if minutes == 0:
        return f"{number_words(hours)} {ZERO_MINUTES}"
    if minutes < 10:
        return f"{number_words(hours)} ноль {number_words(minutes)}"
    return f"{number_words(hours)} {number_words(minutes)}"


def _value_words(number: str, unit: str | None) -> str:
    gender, one, few, many, after_fraction = UNITS[unit] if unit else ("m", "", "", "", "")
    if "." in number or "," in number:
        whole, fraction = number.replace(",", ".").split(".")
        spoken = decimal_words(whole, fraction)
        return f"{spoken} {after_fraction}".strip()
    value = int(number)
    spoken = number_words(value, gender)
    return f"{spoken} {plural(value, one, few, many)}".strip() if unit else spoken


def _drop_latin_clauses(text: str) -> str:
    """Оборот с непереведённой латиницей выбрасывается целиком: по буквам его читать нельзя."""
    parts = _CLAUSES.split(text)
    kept: list[str] = []
    for index in range(0, len(parts), 2):
        clause = parts[index]
        delimiter = parts[index + 1] if index + 1 < len(parts) else ""
        if _LATIN.search(clause):
            continue
        kept.append(clause + delimiter)
    joined = "".join(kept)
    joined = _EMPTY_CLAUSE.sub("", joined)
    return _SPACES.sub(" ", joined).strip()


def to_speech(text: str) -> str:
    """Речевой вариант проверенного текста: ни цифр, ни латиницы, числа и единицы словами.

    Модель в преобразовании не участвует: оно детерминированно и не меняет ни одного числа.
    Синтезатор Silero пропускает цифры и латиницу молча, поэтому остаток такого вида это отказ.
    """
    spoken = _SPAN.sub(lambda m: f"{m.group(1)} до {m.group(2)}", text)
    spoken = _DATE.sub(lambda m: f"{day_words(int(m.group(3)))} {MONTHS[int(m.group(2)) - 1]}", spoken)
    # После предлога «на» дата стоит в винительном падеже: «по данным на десятое мая», а не «на десятого мая».
    spoken = _ON_DAY.sub(lambda m: f"{m.group(1)}{_accusative_day(m.group(2))} ", spoken)
    spoken = _TIME.sub(lambda m: time_words(int(m.group(1)), int(m.group(2))), spoken)
    spoken = _VALUE.sub(lambda m: _value_words(m.group(1), m.group(2)), spoken)
    spoken = _KP_PREFIX.sub("ка-пэ ", spoken)
    spoken = _TERMS.sub(lambda m: TERMS[m.group(1)], spoken)
    spoken = spoken.replace("%", " процентов").replace("°", " градусов")
    spoken = _drop_latin_clauses(spoken)
    if not spoken or _LATIN_OR_DIGIT.search(spoken):
        raise SpeechNotSpeakable(f"в речевом тексте остались цифры или латиница: {spoken!r}")
    return spoken
