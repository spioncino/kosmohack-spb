"""Объяснение рекомендации обычными словами (D76, D78): без кодов уровней и решений.

Минуты называются по каждой линии (D84): линии идут одновременно, и сумма их минут, которую сравнивает `reason`,
бывает больше длины окна. Суммы решают только, какое окно лучше, и числом здесь не звучат.
"""

from collections import Counter
from datetime import datetime, timedelta

from evarisk.types import Level, Recommendation, Window, WindowScore
from evarisk.windows.measures import (
    CRITICAL_REASON,
    GAP_REASON,
    ILLUMINATION_NO_DATA,
    ILLUMINATION_SHADOW_START,
    MISSING_REASON,
    SUNLIT_REASON,
    elevated_hours,
    exposure_hours,
)

DANGER: tuple[Level, ...] = ("high", "critical")

LINE_NAMES = {
    "sep_protons": "протоны",
    "meteor_showers": "метеоры",
    "conjunctions": "сближения",
    "dose_proxy": "прокси дозы",
    "illumination": "освещённость",
    "proton_access_geometry": "геометрия доступа",
}
# Название линии после «по»: «нет данных по протонам».
LINE_DATIVE = {
    "sep_protons": "протонам",
    "meteor_showers": "метеорам",
    "conjunctions": "сближениям",
    "dose_proxy": "прокси дозы",
    "illumination": "освещённости",
    "proton_access_geometry": "геометрии доступа",
}
# Название линии после «уровень»: «критический уровень протонов».
LINE_GENITIVE = {
    "sep_protons": "протонов",
    "meteor_showers": "метеоров",
    "conjunctions": "сближений",
    "dose_proxy": "прокси дозы",
    "illumination": "освещённости",
    "proton_access_geometry": "геометрии доступа",
}
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
# Смысл уровней по линиям штрафа, по правилам линий: sep._point_level (D63, D71), meteors (D67),
# conjunctions.conjunction_level (D53). Опасный уровень это high и critical, повышенный это elevated.
DANGER_MEANING = {
    "sep_protons": "протоны высокой энергии достают станцию",
    "meteor_showers": "частиц, способных пробить скафандр, во много раз больше обычного",
    "conjunctions": "опасное сближение с объектом, вероятность столкновения может достигать красного порога",
}
RAISED_MEANING = {
    "sep_protons": (
        "протонов больше обычного, но опасная часть до экипажа не доходит: станцию закрывает магнитное поле Земли "
        "или протонов высокой энергии мало"
    ),
    "meteor_showers": "частиц, способных пробить скафандр, больше обычного",
    "conjunctions": "рядом проходит объект, вероятность столкновения может достигать жёлтого порога",
}
OTHER_MEANING = "уровень выше спокойного"
AVAILABILITY_PLAIN = {
    "missing": "нет данных по {line}",
    "stale": "данные по {line} устарели",
    "unusable_for_replay": "нет архивных данных по {line}",
}
NEAREST = "Среди окон, где опасность наименьшая, это ближайшее к запрошенному."
KEPT = "перенос заметного выигрыша не даёт"
# Незнакомая причина исключения остаётся как есть и в сводке по окнам идёт после двоеточия.
OTHER_REASON = "причина: "


def line_name(code: str) -> str:
    return LINE_NAMES.get(code, code)


def cap(text: str) -> str:
    """Первая буква заглавная, остальные как есть: `str.capitalize` испортил бы «UTC» и «Земли»."""
    return text[:1].upper() + text[1:]


def _minutes(hours: float) -> int:
    return round(hours * 60)


def plural(n: int, one: str, few: str, many: str) -> str:
    """Форма слова после числа: 1 окно, 3 окна, 5 окон, 21 окно, 11 окон."""
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def joined(items: list[str]) -> str:
    """«а», «а и б», «а, б и в»."""
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} и {items[-1]}"


def day_text(moment: datetime) -> str:
    """«11 мая»."""
    return f"{moment.day} {MONTHS[moment.month - 1]}"


def span_text(window: Window) -> str:
    """«10:45–16:45 UTC»; окно, которое кончается в полночь, тоже без дат: «18:00–00:00 UTC».

    Окно, которое переходит через полночь, называет оба дня: «22:00 11 мая – 04:00 12 мая UTC».
    """
    if window.start.date() == (window.end - timedelta(microseconds=1)).date():
        return f"{window.start:%H:%M}–{window.end:%H:%M} UTC"
    return f"{window.start:%H:%M} {day_text(window.start)} – {window.end:%H:%M} {day_text(window.end)} UTC"


def duration_text(minutes: int) -> str:
    """Длительность для человека: «4 ч 42 мин», «2 ч», «45 мин»; тот же вид у сводки для экипажа."""
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours} ч {rest} мин"
    return f"{hours} ч" if hours else f"{rest} мин"


def shift_text(requested: datetime, chosen: datetime) -> str:
    """«на 2 ч 15 мин раньше» или «на 4 ч позже»."""
    minutes = round((chosen - requested).total_seconds() / 60)
    if minutes == 0:
        return "в то же время"
    word = "позже" if minutes > 0 else "раньше"
    return f"на {duration_text(abs(minutes))} {word}"


def _reason_parts(reason: str) -> list[tuple[str, str]]:
    """Части причины исключения окна: что случилось без чисел и то же с числами; незнакомая часть как есть."""
    parts: list[tuple[str, str]] = []
    for part in (p for p in reason.split("; ") if p):
        if m := CRITICAL_REASON.match(part):
            what = f"критический уровень {LINE_GENITIVE.get(m.group(1), line_name(m.group(1)))}"
            parts.append((what, f"{what}, всего {duration_text(_minutes(float(m.group(2))))}"))
        elif m := MISSING_REASON.match(part):
            template = AVAILABILITY_PLAIN.get(m.group(2), AVAILABILITY_PLAIN["missing"])
            what = template.format(line=LINE_DATIVE.get(m.group(1), line_name(m.group(1))))
            parts.append((what, what))
        elif m := GAP_REASON.match(part):
            what = f"пробел данных по {LINE_DATIVE.get(m.group(1), line_name(m.group(1)))}"
            parts.append((what, f"{what}, {duration_text(int(m.group(2)))}"))
        elif m := SUNLIT_REASON.match(part):
            sunlit, limit = float(m.group(1)), float(m.group(2))
            parts.append(("мало света", f"мало света: на свету {sunlit:.0%} окна, а задано не меньше {limit:.0%}"))
        elif part == ILLUMINATION_SHADOW_START:
            parts.append(("выход начинается в тени", "выход начинается в тени, а задан старт на свету"))
        elif part == ILLUMINATION_NO_DATA:
            parts.append(("нет данных об освещённости", "нет данных об освещённости"))
        else:
            parts.append((f"{OTHER_REASON}{part}", part))
    return parts


def plain_reason(reason: str) -> str:
    """Причина исключения окна обычными словами; незнакомая часть остаётся как есть."""
    return "; ".join(full for _, full in _reason_parts(reason))


def _exclusions(scores: list[WindowScore]) -> list[str]:
    """«В 38 окнах из 73 критический уровень протонов.»: причины исключения по числу окон с ними."""
    counts: Counter[str] = Counter()
    for s in scores:
        counts.update(list(dict.fromkeys(what for what, _ in _reason_parts(s.excluded_reason or ""))))
    total = len(scores)
    out: list[str] = []
    for key, n in counts.most_common():
        other = key.startswith(OTHER_REASON)
        what = f": {key.removeprefix(OTHER_REASON)}" if other else f" {key}"
        if total == 1:
            out.append(f"{'Причина' if other else 'В нём'}{what}.")
        elif n == total:
            out.append(f"Во всех {n} окнах{what}.")
        else:
            out.append(f"В {n} {plural(n, 'окне', 'окнах', 'окнах')} из {total}{what}.")
    return out


def _line_minutes(s: WindowScore, levels: tuple[Level, ...]) -> dict[str, int]:
    return {line: _minutes(sum(hours[lv] for lv in levels)) for line, hours in s.per_line.items()}


def _amount(minutes: int) -> str:
    return duration_text(minutes) if minutes else "нет"


def _by_line(
    levels: tuple[Level, ...], meaning: dict[str, str] | None, a: WindowScore, b: WindowScore | None
) -> list[str]:
    """По строке на линию: «Протоны: в запрошенном окне 4 ч 42 мин, в новом 2 ч 27 мин. Протонов больше …».

    Без `meaning` строка кончается минутами: так звучат минуты без данных.
    """
    first, second = _line_minutes(a, levels), _line_minutes(b, levels) if b is not None else {}
    out: list[str] = []
    for line in first.keys() | second.keys():
        x, y = first.get(line, 0), second.get(line, 0)
        what = "" if meaning is None else f" {cap(meaning.get(line, OTHER_MEANING))}."
        if b is None and x:
            out.append(f"{cap(line_name(line))}: {duration_text(x)}.{what}")
        elif b is not None and x != y:
            out.append(f"{cap(line_name(line))}: в запрошенном окне {_amount(x)}, в новом {_amount(y)}.{what}")
    return sorted(out)


def _lines_short(s: WindowScore, levels: tuple[Level, ...]) -> str:
    """«протоны 40 мин, метеоры 20 мин»: минуты линий окна, где они есть, каждая линия отдельно (D84)."""
    return ", ".join(
        f"{line_name(line)} {duration_text(m)}" for line, m in sorted(_line_minutes(s, levels).items()) if m
    )


# Ноль опасных минут по прогнозу не звучит как измеренный (D84): поток протонов не измерен всё окно или часть окна.
FORECAST_SAID = {
    "all": "по прогнозу, измерений потока протонов нет",
    "some": "частью по прогнозу, поток протонов измерен не всё время",
}
ZERO_KEPT = "Опасных минут в окне нет."
ZERO_BOTH = "Опасных минут нет ни в запрошенном, ни в новом окне."
ZERO_NEW = "В новом окне опасных минут нет."
ZERO_NEW_LIST = "В новом окне опасных минут нет:"


def _joint(a: str, b: str) -> str:
    """Состояние потока для фразы о двух окнах: «all» только при неизмеренном потоке в обоих."""
    if not a and not b:
        return ""
    return "all" if a == b == "all" else "some"


def forecast_zero(explanation: tuple[str, ...], requested: str, chosen: str) -> tuple[str, ...]:
    """Фраза «опасных минут нет» получает оговорку, когда в окне поток протонов не измерен (D64, D84).

    `requested` и `chosen` это состояния из `measures.unmeasured_state`: «all», «some» или пусто. Фраза начинается
    как прежде, «Опасных минут в окне нет, но это по прогнозу, измерений потока протонов нет.», и стоит на том же месте.
    """
    states = {ZERO_KEPT: requested, ZERO_BOTH: _joint(requested, chosen), ZERO_NEW: chosen, ZERO_NEW_LIST: chosen}
    out: list[str] = []
    for sentence in explanation:
        state = states.get(sentence, "")
        out.append(f"{sentence[:-1]}, но это {FORECAST_SAID[state]}{sentence[-1]}" if state else sentence)
    return tuple(out)


def _others(rec: Recommendation) -> list[str]:
    n = len(rec.equivalents)
    if not n:
        return []
    windows, alike = plural(n, "окно", "окна", "окон"), plural(n, "равноценно", "равноценны", "равноценны")
    return [f"Ещё {n} {windows} почти {alike} выбранному."]


def before_tail(explanation: tuple[str, ...], rec: Recommendation, sentence: str) -> tuple[str, ...]:
    """Фраза перед концом объяснения: выбор ближайшего окна и число равноценных окон остаются последними."""
    if not sentence:
        return explanation
    tail = {NEAREST, *_others(rec)}
    cut = len(explanation)
    while cut > 1 and explanation[cut - 1] in tail:
        cut -= 1
    return (*explanation[:cut], sentence, *explanation[cut:])


def _kept(scores: list[WindowScore], rec: Recommendation, requested: WindowScore) -> list[str]:
    out = [f"Рекомендуем оставить запрошенное окно {span_text(requested.window)}: {KEPT}."]
    minutes = _minutes(exposure_hours(requested))
    if minutes:
        out.append("Опасные минуты в окне, по каждой линии отдельно:")
        out.extend(_by_line(DANGER, DANGER_MEANING, requested, None))
        eligible = [s for s in scores if s.excluded_reason is None]
        best = min(eligible, key=exposure_hours, default=requested)
        fewer = _minutes(exposure_hours(best))
        # Окно сохраняется только при тех же целых опасных минутах, что у лучшего (D79); иначе фраза называет числа
        # лучшего окна по линиям: сумма линий длительностью не называется (D84).
        if fewer >= minutes:
            out.append("Меньше опасных минут нет ни в одном подходящем окне.")
        else:
            out.append(
                f"В лучших подходящих окнах их меньше: {_lines_short(best, DANGER)}."
                if fewer
                else "В лучших подходящих окнах их нет."
            )
    else:
        out.append(ZERO_KEPT)
    raised = _by_line(("elevated",), RAISED_MEANING, requested, None)
    if raised:
        out.append("Повышенный фон в окне:")
        out.extend(raised)
    return [*out, *_others(rec)]


def _moved(requested: WindowScore, chosen: WindowScore) -> list[str]:
    # Какое окно лучше, решают суммы по линиям, как в выборе окна; числами звучат минуты каждой линии (D84).
    danger_req, danger_new = _minutes(exposure_hours(requested)), _minutes(exposure_hours(chosen))
    out: list[str] = []
    if danger_req != danger_new:
        out.append(ZERO_NEW_LIST if danger_new == 0 else "В новом окне опасных минут меньше:")
        out.extend(_by_line(DANGER, DANGER_MEANING, requested, chosen))
    else:
        if danger_req == 0:
            out.append(ZERO_BOTH)
        elif _line_minutes(requested, DANGER) == _line_minutes(chosen, DANGER):
            out.append(f"Опасных минут в обоих окнах поровну: {_lines_short(requested, DANGER)}.")
        else:
            out.append("Опасные минуты по линиям:")
            out.extend(_by_line(DANGER, DANGER_MEANING, requested, chosen))
        raised = _by_line(("elevated",), RAISED_MEANING, requested, chosen)
        if raised:
            fewer = _minutes(elevated_hours(chosen)) < _minutes(elevated_hours(requested))
            out.append("Зато в новом окне меньше повышенного фона:" if fewer else "Повышенный фон в окнах:")
            out.extend(raised)
    unknown = _by_line(("unknown",), None, requested, chosen)
    if unknown:
        out.append("Минуты без данных:")
        out.extend(unknown)
    return out


def explain(scores: list[WindowScore], rec: Recommendation, requested: WindowScore | None) -> tuple[str, ...]:
    """Решение первой фразой, дальше причины по линиям: опасные минуты, повышенный фон, пробелы данных.

    Сохранённое окно имеет те же целые опасные минуты, что лучшее (D79), поэтому допуск в тексте не называется.
    """
    if rec.insufficient or rec.preferred is None:
        n = len(scores)
        head = (
            "Подходящего окна нет: единственное окно исключено."
            if n == 1
            else f"Подходящего окна нет: исключены все {n} {plural(n, 'окно', 'окна', 'окон')}."
        )
        return (head, *_exclusions(scores))
    if requested is not None and rec.preferred == requested.window:
        return tuple(_kept(scores, rec, requested))
    chosen = next((s for s in scores if s.window == rec.preferred), None)
    if chosen is None or requested is None:
        return (f"Рекомендуем окно {span_text(rec.preferred)}.", *_others(rec))
    head = (
        f"Рекомендуем перенести выход на {span_text(chosen.window)}, "
        f"{shift_text(requested.window.start, chosen.window.start)}."
    )
    if requested.excluded_reason is not None:
        danger = _lines_short(chosen, DANGER)
        return (
            head,
            f"Запрошенное окно {span_text(requested.window)} не подходит: {plain_reason(requested.excluded_reason)}.",
            f"В новом окне опасные минуты по линиям: {danger}." if danger else ZERO_NEW,
            *_others(rec),
        )
    return (head, *_moved(requested, chosen), NEAREST, *_others(rec))
