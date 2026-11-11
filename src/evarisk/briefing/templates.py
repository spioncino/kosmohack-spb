from collections.abc import Callable

from evarisk.briefing.facts import SEP_LINE, duration, hm, num, shift_minutes, ymd
from evarisk.briefing.models import BriefingFacts, Exclusion, ExposedInterval, FactWindow, Question
from evarisk.windows.explain import cap, joined, plural

# Речевой стиль текста (D77, D78): сообщение слушают по связи один раз, поэтому короткие законченные фразы обычными
# словами, без двоеточий посреди фразы, точек с запятой, скобок, кодов и латиницы. Синтез речи не склоняет
# числительные, поэтому число стоит там, где начальная форма звучит правильно: «опасных минут 74», «исключены 38 окон»,
# а не «вместо 282». Повышение версии пересобирает сводки из кеша. Версия 4 (D84): минуты окон называются по линиям,
# сумма по линиям длительностью не называется.
STYLE = 4
CLOSING = "Решение за руководителем работ."
LOW_CONFIDENCE = "Уверенность в оценке протонов низкая."
# Предел длины сообщения в словах; его же проверяет `validate` у текста модели и у шаблона.
MAX_WORDS = 70
NO_MINUTES = "не посчитаны, данных для сравнения окон нет"
LINE_NAMES = {
    "sep_protons": "протоны",
    "meteor_showers": "метеорные потоки",
    "conjunctions": "сближения",
    "dose_proxy": "прокси дозы",
    "illumination": "освещённость",
    "proton_access_geometry": "геометрия доступа протонов",
}
# «По протонам данных нет»: название линии после «по».
LINE_DATIVE = {
    "sep_protons": "протонам",
    "meteor_showers": "метеорным потокам",
    "conjunctions": "сближениям",
    "dose_proxy": "прокси дозы",
    "illumination": "освещённости",
    "proton_access_geometry": "геометрии доступа протонов",
}
LEVEL_NAMES = {
    "none": "фон",
    "elevated": "повышенный",
    "high": "высокий",
    "critical": "критический",
    "unknown": "нет уровня",
}
# «По протонам данных нет.»: состояние данных линии после её названия.
GAP_SPOKEN = {
    "missing": "данных нет",
    "stale": "данные устарели",
    "unusable_for_replay": "архивных данных нет",
}
# Источники по-русски: имя источника латиницей синтез речи прочесть не может.
SOURCE_SPOKEN = {
    "nasa_oem": "эфемерида станции",
    "celestrak": "орбитальные элементы",
    "swpc_live": "оперативные данные космической погоды",
    "donki": "уведомления о солнечных событиях",
    "gfz_kp": "индекс геомагнитной активности",
    "socrates": "отчёты о сближениях",
    "meteor_docs": "прогнозы метеорных потоков",
    "goes_sgps": "спутниковые измерения протонов",
    "swpc_archive": "архив космической погоды",
}
OTHER_SOURCE = "ещё один источник данных"
# Что происходит на опасных минутах, по линии и уровню: sep._point_level (D63, D71), meteors (D67),
# conjunctions.conjunction_level (D53).
DANGER_SPOKEN = {
    ("sep_protons", "high"): "протоны достают станцию",
    ("sep_protons", "critical"): "протоны самой высокой энергии достают станцию",
    ("meteor_showers", "high"): "идёт сильный метеорный поток",
    ("meteor_showers", "critical"): "идёт очень сильный метеорный поток",
    ("conjunctions", "high"): "опасное сближение с объектом",
    ("conjunctions", "critical"): "опасное сближение с объектом",
}
# Что значит повышенный фон по линии; без цифр, проверка текста модели принимает только числа из фактов.
RAISED_SPOKEN = {
    "sep_protons": "протонов больше обычного, но опасная часть до экипажа не доходит",
    "meteor_showers": "метеорных частиц больше обычного",
    "conjunctions": "рядом проходит объект, и риск столкновения нельзя исключить",
}
# Общая фраза о смысле повышенного фона для фактов без списка линий (сводки до D77).
ELEVATED_MEANING = "обстановка хуже спокойной, но ниже опасной"
# Неизмеренный поток протонов это пробел данных (D64, D78): уровень на этих минутах не из измерений.
FROM_FORECAST = "уровень взят из прогнозов и сообщений о событиях"
WHY_UNMEASURED = "поток протонов не измерен"
WHY_FORECAST = "обстановка после момента данных взята из прогноза"
WHY_STORM = "поправку на магнитную бурю применить не удалось"
NO_GAPS = "Пробелов в данных нет."
INTERVALS_SHOWN = 3
GAPS_SHOWN = 3
BASIS_REPLAY = "Прогноз по данным на {moment} UTC."
BASIS_REVIEW = "Это разбор по архиву, а не прогноз."
BASIS_CURRENT = "Оценка по данным на {moment}."


def line_name(line: str) -> str:
    return LINE_NAMES.get(line, line)


def words(text: str) -> int:
    return len([word for word in text.split() if any(ch.isalnum() for ch in word)])


def line_dative(line: str) -> str:
    return LINE_DATIVE.get(line, line_name(line))


def by_line(minutes: dict[str, float], spoken: Callable[[float], str] = num) -> str:
    """«по протонам 74, по метеорным потокам 30»: минуты каждой линии отдельно, линии без минут не называются.

    Линии идут одновременно, поэтому их минуты не складываются (D84): сумма бывает больше длины окна.
    """
    return ", ".join(f"по {line_dative(line)} {spoken(value)}" for line, value in minutes.items() if value)


def danger_by_line(minutes: dict[str, float]) -> str:
    """Опасные минуты окна по линиям числом, «по протонам 74»; без них «нет»."""
    return by_line(minutes) or "нет"


def _nonzero(minutes: dict[str, float]) -> dict[str, float]:
    return {line: value for line, value in minutes.items() if value}


def compared_by_line(old: dict[str, float], new: dict[str, float], what: str) -> list[str]:
    """Минуты запрошенного и нового окна по линиям, где они разные; первой идёт линия с наибольшим выигрышем.

    «Зато по протонам повышенного фона в новом 3 ч 57 мин, а в запрошенном 6 ч.», где `what` это «повышенного
    фона»; следующие линии идут отдельными фразами «По метеорным потокам в новом …».
    """
    lines = [line for line in dict.fromkeys([*old, *new]) if old.get(line, 0) != new.get(line, 0)]
    lines.sort(key=lambda line: new.get(line, 0) - old.get(line, 0))
    out: list[str] = []
    for i, line in enumerate(lines):
        was, now = old.get(line, 0), new.get(line, 0)
        now_text = "нет" if now == 0 else duration(now)
        was_text = "нет" if was == 0 else duration(was)
        head = f"Зато по {line_dative(line)} {what}" if i == 0 else f"По {line_dative(line)}"
        out.append(f"{head} в новом {now_text}, а в запрошенном {was_text}.")
    return out


# Ноль опасных минут в окне с неизмеренным потоком протонов звучит как счёт по прогнозу (D64, D84).
FORECAST_ALL = "измерений потока нет"
FORECAST_SOME = "поток протонов измерен не всё время"


def flux_state(window: FactWindow | None) -> str:
    """«all», когда поток протонов не измерен всё окно, «some», когда часть окна, иначе пусто."""
    if window is None or not window.flux_unmeasured_minutes:
        return ""
    return "all" if window.flux_unmeasured_minutes >= round(window.duration_hours * 60.0) else "some"


def forecast_tail(*windows: FactWindow | None) -> str:
    """Оговорка к нулю опасных минут: «измерений потока нет» или «поток протонов измерен не всё время»."""
    states = [flux_state(window) for window in windows]
    if not any(states):
        return ""
    return FORECAST_ALL if all(state == "all" for state in states) else FORECAST_SOME


def _from_to(start_hm: str, end_hm: str) -> str:
    return f"с {start_hm} до {end_hm}"


def requested_span(facts: BriefingFacts) -> str:
    """«с 04:00 до 10:00»: так время окна читается вслух без тире."""
    return _from_to(hm(facts.requested.start), hm(facts.requested.end))


def basis_text(facts: BriefingFacts) -> str:
    """Короткое основание данных для текста; полная формулировка лежит в `facts.basis`."""
    if facts.mode == "replay" and facts.cutoff is not None:
        return BASIS_REPLAY.format(moment=f"{ymd(facts.cutoff)} {hm(facts.cutoff)}")
    if facts.mode == "review":
        return BASIS_REVIEW
    moment = facts.data_moment
    return BASIS_CURRENT.format(moment="момент запроса" if moment is None else f"{ymd(moment)} {hm(moment)} UTC")


def raised_meaning(facts: BriefingFacts) -> str:
    """Что значит повышенный фон в этих окнах: по линиям, где он есть, иначе общая фраза."""
    lines = list(facts.requested.elevated_lines)
    if facts.recommended is not None:
        lines += [line for line in facts.recommended.elevated_lines if line not in lines]
    known = [RAISED_SPOKEN[line] for line in lines if line in RAISED_SPOKEN]
    return joined(known) if known else ELEVATED_MEANING


def shift_word(facts: BriefingFacts) -> str:
    """«раньше» или «позже»: сдвиг рекомендованного окна относительно запрошенного."""
    if facts.recommended is None:
        return ""
    return "раньше" if facts.recommended.start < facts.requested.start else "позже"


def _accusative_reads_right(minutes: int) -> bool:
    """После «на» число стоит в винительном падеже: «на двадцать одну минуту». Синтез прочтёт «на двадцать одна
    минута», поэтому минуты на 1, кроме 11, вслух не называются."""
    rest = minutes % 60
    return not (rest % 10 == 1 and rest % 100 != 11)


def shift_phrase(facts: BriefingFacts) -> str:
    """«на 2 ч 15 мин раньше»; без готовой длительности, которую можно прочесть вслух, просто «раньше»."""
    word = shift_word(facts)
    minutes = shift_minutes(facts.requested, facts.recommended)
    if not word or minutes is None or not _accusative_reads_right(int(minutes)):
        return word
    return f"на {duration(minutes)} {word}"


def clear_text(facts: BriefingFacts) -> str:
    """«После 07:52 и до конца периода опасных минут нет.»; при переносе в чистое окно фраза сбивает с толку."""
    if facts.clear_after is None:
        return ""
    if facts.verdict == "move_to" and facts.recommended is not None and facts.recommended.exposure_minutes == 0:
        return ""
    return f"После {hm(facts.clear_after)} и до конца периода опасных минут нет."


def danger_list(facts: BriefingFacts) -> list[str]:
    """Опасные отрезки запрошенного окна словами: «Протоны достают станцию с 04:36 до 04:50 и с 05:24 до 05:33.»."""
    groups: dict[tuple[str, str], list[ExposedInterval]] = {}
    for item in facts.exposed[:INTERVALS_SHOWN]:
        groups.setdefault((item.line, item.level), []).append(item)
    sentences = []
    for (line, level), items in groups.items():
        what = DANGER_SPOKEN.get((line, level), f"{line_name(line)}, уровень {LEVEL_NAMES.get(level, level)}")
        spans = joined([_from_to(hm(item.start), hm(item.end)) for item in items])
        sentences.append(f"{cap(what)} {spans}.")
    if facts.exposed_total > INTERVALS_SHOWN:
        sentences.append(f"Всего таких отрезков {num(facts.exposed_total)}.")
    return sentences


def danger_sentences(facts: BriefingFacts) -> str:
    return " ".join(danger_list(facts))


def _sources_spoken(names: tuple[str, ...]) -> str:
    known = [SOURCE_SPOKEN[name] for name in names if name in SOURCE_SPOKEN]
    others = len(names) - len(known)
    if others:
        known.append(OTHER_SOURCE if others == 1 else "другие источники данных")
    return joined(known)


def _depends_unmeasured(facts: BriefingFacts) -> bool:
    depends = (facts.confidence_depends_on or "").lower()
    return "не измерен" in depends or "измерений потока" in depends or "нет ряда" in depends


def _flux_gap_named(facts: BriefingFacts, said: frozenset[str]) -> bool:
    """Отсутствие данных по протонам уже звучит: в причинах исключения окон или в пробелах данных."""
    return SEP_LINE in said or any(gap.line == SEP_LINE for gap in facts.gaps[:GAPS_SHOWN])


def unmeasured_sentence(facts: BriefingFacts, said: frozenset[str] = frozenset()) -> str:
    """Неизмеренный поток протонов как пробел данных (D78): говорится один раз и никогда рядом с «пробелов нет»."""
    if _flux_gap_named(facts, said):
        return ""
    minutes = facts.unmeasured_minutes
    if minutes:
        if facts.search_minutes is not None and minutes >= facts.search_minutes:
            return f"Поток протонов за весь период не измерен, {FROM_FORECAST}."
        return f"Поток протонов не измерен на части периода, всего {duration(minutes)}, там {FROM_FORECAST}."
    if facts.confidence_grade == "low" and _depends_unmeasured(facts):
        return f"Поток протонов не измерен, {FROM_FORECAST}."
    return ""


def gaps_list(facts: BriefingFacts, said: frozenset[str] = frozenset(), shown: int | None = GAPS_SHOWN) -> list[str]:
    """Пробелы данных словами; молчать о них нельзя: отказ источника это не спокойная обстановка.

    `said` это линии, о нехватке данных по которым уже сказано в причинах исключения окон.
    """
    sentences = [
        f"По {LINE_DATIVE.get(gap.line, line_name(gap.line))} {GAP_SPOKEN.get(gap.availability, 'данных нет')}."
        for gap in facts.gaps[:shown]
        if gap.line not in said
    ]
    unmeasured = unmeasured_sentence(facts, said)
    if unmeasured:
        sentences.append(unmeasured)
    unknown = by_line(facts.requested.unknown_by_line, duration)
    if unknown:
        sentences.append(f"В запрошенном окне без данных {unknown}.")
    if facts.disabled_sources:
        sentences.append(f"Вручную отключены {_sources_spoken(facts.disabled_sources)}.")
    return sentences


def confidence_spoken(facts: BriefingFacts) -> str:
    """Почему оценка протонов ненадёжна, простыми словами, по тексту `depends_on` линии протонов."""
    depends = (facts.confidence_depends_on or "").lower()
    if _depends_unmeasured(facts):
        return WHY_UNMEASURED
    if "прогноз" in depends:
        return WHY_FORECAST
    if "поправка на бурю" in depends:
        return WHY_STORM
    return ""


def low_confidence(facts: BriefingFacts, said: frozenset[str] = frozenset()) -> str:
    """«Уверенность в оценке протонов низкая, …»; причина не повторяет уже сказанное о неизмеренном потоке."""
    if facts.confidence_grade != "low":
        return ""
    why = confidence_spoken(facts)
    flux_said = _flux_gap_named(facts, said) or bool(unmeasured_sentence(facts, said))
    if flux_said and why in (WHY_UNMEASURED, WHY_FORECAST):
        why = ""
    return f"{LOW_CONFIDENCE[:-1]}, {why}." if why else LOW_CONFIDENCE


# Часть сообщения и признак «можно опустить»: необязательные части выпадают с конца, если текст длиннее MAX_WORDS.
Part = tuple[str, bool]


def _must(*texts: str) -> list[Part]:
    return [(text, False) for text in texts if text]


def _may(*texts: str) -> list[Part]:
    return [(text, True) for text in texts if text]


def _fit(parts: list[Part]) -> str:
    kept = [part for part in parts if part[0]]
    while words(" ".join(text for text, _ in kept)) > MAX_WORDS:
        optional = [i for i, (_, may) in enumerate(kept) if may]
        if not optional:
            break
        del kept[optional[-1]]
    return " ".join(text for text, _ in kept)


def _tail(facts: BriefingFacts, said: frozenset[str] = frozenset(), *, name_no_gaps: bool = False) -> list[Part]:
    """Пробелы данных, основание данных, низкая уверенность и последняя фраза: они звучат всегда."""
    gaps = gaps_list(facts, said)
    if not gaps and name_no_gaps:
        gaps = [NO_GAPS]
    return _must(*gaps, basis_text(facts), low_confidence(facts, said), CLOSING)


def _raised_reason(facts: BriefingFacts) -> list[Part]:
    """Причина переноса, когда опасных минут в окнах поровну: меньше повышенного фона или меньше минут без данных.

    Без этой фразы сводка называет равные опасные минуты и не объясняет, зачем переносить окно.
    """
    rec, req = facts.recommended, facts.requested
    if facts.verdict != "move_to" or rec is None:
        return []
    # Какое окно лучше, решают суммы по линиям, как в выборе окна; называются минуты по линиям (D84).
    old, new = req.elevated_minutes, rec.elevated_minutes
    if old is not None and new is not None and new < old:
        # Числа в начальной форме: синтез речи не склоняет «вместо двухсот восьмидесяти двух».
        # Первая линия и смысл повышенного фона звучат всегда, прочие линии выпадают при нехватке слов.
        head = compared_by_line(req.elevated_by_line, rec.elevated_by_line, "повышенного фона")
        return [*_must(*head[:1]), *_may(*head[1:]), *_must(f"Повышенный фон значит, что {raised_meaning(facts)}.")]
    old, new = req.unknown_minutes, rec.unknown_minutes
    if old is not None and new is not None and new < old:
        return _must(
            f"Зато в новом окне данные есть на всё время, а в запрошенном без данных "
            f"{by_line(req.unknown_by_line, duration)}."
            if new == 0
            else f"Зато в новом окне без данных {by_line(rec.unknown_by_line, duration)}, а в запрошенном "
            f"{by_line(req.unknown_by_line, duration)}."
        )
    return []


def _minutes_compared(facts: BriefingFacts) -> list[Part]:
    """Опасные минуты запрошенного и нового окна одной фразой, дальше отрезки или повышенный фон.

    Если запрошенное окно исключено, фраза называет и причину: «В запрошенном окне мало света, …». Минуты
    называются по линиям (D84): «опасных минут по протонам 74»; равенство и разница окон судятся по сумме, как в выборе.
    """
    req = facts.requested.exposure_minutes
    rec = None if facts.recommended is None else facts.recommended.exposure_minutes
    why = joined([_exclusion_what(e) for e in facts.requested_exclusions])
    if req is None or rec is None or facts.recommended is None:
        return _must(f"В запрошенном окне {why}." if why else "", f"Опасные минуты {NO_MINUTES}.")
    old, new_lines = facts.requested.exposure_by_line, facts.recommended.exposure_by_line
    if req != rec:
        new = f"в новом {danger_by_line(new_lines)}"
        if rec == 0:
            new = "в новом по прогнозу их нет" if forecast_tail(facts.recommended) else "в новом их нет"
        first = f"В запрошенном окне {why + ', ' if why else ''}опасных минут {danger_by_line(old)}, {new}."
        return [*_must(first), *_may(*danger_list(facts))]
    if why:
        return _must(f"В запрошенном окне {why}.")
    if req == 0:
        tail = forecast_tail(facts.requested, facts.recommended)
        zero = (
            f"По прогнозу опасных минут нет ни в запрошенном окне, ни в новом, {tail}."
            if tail
            else "Опасных минут нет ни в запрошенном окне, ни в новом."
        )
        return [*_must(zero), *_raised_reason(facts)]
    equal = (
        f"Опасных минут в обоих окнах поровну, {danger_by_line(old)}."
        if _nonzero(old) == _nonzero(new_lines)
        else f"Опасных минут в запрошенном окне {danger_by_line(old)}, в новом {danger_by_line(new_lines)}."
    )
    return [*_must(equal), *_raised_reason(facts)]


def _requested_minutes(facts: BriefingFacts, *, raised: bool = False) -> list[Part]:
    """Опасные минуты и повышенный фон запрошенного окна, которое остаётся."""
    minutes = facts.requested.exposure_minutes
    if minutes is None:
        parts = _must(f"Опасные минуты {NO_MINUTES}.")
    elif minutes == 0:
        tail = forecast_tail(facts.requested)
        parts = _must(f"По прогнозу опасных минут в нём нет, {tail}." if tail else "Опасных минут в нём нет.")
    else:
        parts = [
            *_must(f"Опасных минут в нём {danger_by_line(facts.requested.exposure_by_line)}."),
            *_may(*danger_list(facts)),
        ]
    elevated = by_line(facts.requested.elevated_by_line, duration)
    if elevated:
        text = f"Повышенного фона в окне {elevated}, то есть {raised_meaning(facts)}."
        parts += _must(text) if raised else _may(text)
    return parts


EXCLUSION_SPOKEN = {
    "critical": "критический уровень по {line}",
    "data_gap": "пропуски в данных по {line}",
    "no_data": "нет данных по {line}",
    "light": "мало света",
    "shadow_start": "старт в тени",
    "no_light_data": "нет данных об освещённости",
    "other": "другие причины",
}
# Причина исключения после «из-за»: «Из-за критического уровня протонов исключены 38 окон.».
EXCLUSION_CAUSE = {
    "critical": "из-за критического уровня по {line}",
    "data_gap": "из-за пропусков в данных по {line}",
    "no_data": "из-за отсутствия данных по {line}",
    "light": "из-за нехватки света",
    "shadow_start": "из-за старта в тени",
    "no_light_data": "из-за отсутствия данных об освещённости",
    "other": "по другим причинам",
}
# Критический уровень словами линии: «критический уровень протонов», «очень сильный метеорный поток».
CRITICAL_SPOKEN = {
    "sep_protons": ("критический уровень протонов", "из-за критического уровня протонов"),
    "meteor_showers": ("очень сильный метеорный поток", "из-за очень сильного метеорного потока"),
    "conjunctions": ("опасное сближение с объектом", "из-за опасного сближения с объектом"),
}
DATA_SHORTAGE = frozenset({"data_gap", "no_data", "no_light_data"})
EXCLUSIONS_SHOWN = 2


def _exclusion_what(item: Exclusion, cause: bool = False) -> str:
    if item.kind == "critical" and item.line in CRITICAL_SPOKEN:
        return CRITICAL_SPOKEN[item.line][1 if cause else 0]
    line = "" if item.line is None else LINE_DATIVE.get(item.line, line_name(item.line))
    return (EXCLUSION_CAUSE if cause else EXCLUSION_SPOKEN)[item.kind].format(line=line)


def data_short(facts: BriefingFacts) -> bool:
    """Окна исключены из-за нехватки данных, а не из-за опасности; без списка причин (сводки до D77) да."""
    return bool(facts.gaps) or not facts.exclusions or any(e.kind in DATA_SHORTAGE for e in facts.exclusions)


def _windows_counted(n: int, total: int) -> str:
    """«исключены все 73 окна», «исключены 38 окон», «исключено 1 окно»: число в начальной форме."""
    noun = plural(n, "окно", "окна", "окон")
    if n == total:
        return "исключено единственное окно" if n == 1 else f"исключены все {num(n)} {noun}"
    verb = "исключено" if n % 10 == 1 and n % 100 != 11 else "исключены"
    return f"{verb} {num(n)} {noun}"


def _requested_covered(facts: BriefingFacts) -> bool:
    """Запрошенное окно исключено по тем же причинам, что и все окна периода: отдельная фраза о нём лишняя."""
    covering = {(e.kind, e.line) for e in facts.exclusions[:EXCLUSIONS_SHOWN] if e.windows == facts.windows_total}
    return bool(facts.requested_exclusions) and all((e.kind, e.line) in covering for e in facts.requested_exclusions)


def exclusion_list(facts: BriefingFacts, requested_too: bool = False) -> list[str]:
    """«Из-за критического уровня протонов исключены 38 окон.»: главные причины исключения окон.

    `requested_too` дописывает «и запрошенное тоже» к последней причине, общей для всех окон.
    """
    shown = facts.exclusions[:EXCLUSIONS_SHOWN]
    last_all = max((i for i, e in enumerate(shown) if e.windows == facts.windows_total), default=None)
    return [
        f"{cap(_exclusion_what(item, cause=True))} {_windows_counted(item.windows, facts.windows_total)}"
        + (", и запрошенное тоже." if requested_too and i == last_all else ".")
        for i, item in enumerate(shown)
    ]


def exclusion_sentences(facts: BriefingFacts) -> str:
    return " ".join(exclusion_list(facts))


def requested_exclusion(facts: BriefingFacts) -> str:
    span = f"Запрошенное окно {requested_span(facts)} UTC"
    if facts.requested_exclusions:
        return f"{span} не подходит, в нём {joined([_exclusion_what(e) for e in facts.requested_exclusions])}."
    return f"{span} оценить нельзя." if data_short(facts) else ""


def _insufficient(facts: BriefingFacts, second: str = "") -> str:
    # Все окна исключены опасностью или нехваткой данных: это разные вещи, и экипаж должен услышать какая.
    why = "данных недостаточно" if data_short(facts) else "все окна в периоде исключены"
    said = frozenset(e.line for e in facts.exclusions[:EXCLUSIONS_SHOWN] if e.kind == "no_data" and e.line)
    covered = not second and _requested_covered(facts)
    return _fit(
        [
            *_must(f"Нет оснований рекомендовать окно, {why}."),
            *_must(second or ("" if covered else requested_exclusion(facts))),
            *_must(*exclusion_list(facts, requested_too=covered)),
            *_tail(facts, said),
        ]
    )


def _move_start(facts: BriefingFacts) -> str:
    return "" if facts.recommended is None else hm(facts.recommended.start)


def _begin(facts: BriefingFacts, lead: str = "Рекомендуем начать выход") -> str:
    """«Рекомендуем начать выход на 2 ч 15 мин раньше, в 10:45 UTC.»."""
    shift = shift_phrase(facts)
    return f"{lead} {shift}, в {_move_start(facts)} UTC." if shift else f"{lead} в {_move_start(facts)} UTC."


KEEP_WHY = "переносить нет смысла"


def _summary(facts: BriefingFacts) -> str:
    if facts.verdict == "insufficient":
        return _insufficient(facts)
    if facts.verdict == "keep_requested":
        return _fit(
            [
                *_must(f"Рекомендуем оставить запрошенное окно {requested_span(facts)} UTC, {KEEP_WHY}."),
                *_requested_minutes(facts),
                *_may(clear_text(facts)),
                *_tail(facts),
            ]
        )
    return _fit([*_must(_begin(facts)), *_minutes_compared(facts), *_may(clear_text(facts)), *_tail(facts)])


def _can_go_now(facts: BriefingFacts) -> str:
    if facts.verdict == "insufficient":
        return _insufficient(facts)
    if facts.verdict == "keep_requested":
        return _fit(
            [
                *_must(f"Запрошенное окно {requested_span(facts)} UTC не хуже других, {KEEP_WHY}."),
                *_requested_minutes(facts),
                *_may(clear_text(facts)),
                *_tail(facts),
            ]
        )
    head = "Запрошенное окно не подходит" if facts.requested_exclusions else "Запрошенное окно хуже"
    return _fit([*_must(_begin(facts, f"{head}, рекомендуем начать")), *_minutes_compared(facts), *_tail(facts)])


def _when_better(facts: BriefingFacts) -> str:
    if facts.verdict == "insufficient":
        return _insufficient(facts, "Назвать лучшее время не можем.")
    if facts.verdict == "keep_requested":
        return _fit(
            [
                *_must(f"Заметно лучшего времени нет, рекомендуем оставить окно {requested_span(facts)} UTC."),
                *_requested_minutes(facts),
                *_may(clear_text(facts)),
                *_tail(facts),
            ]
        )
    end = "" if facts.recommended is None else hm(facts.recommended.end)
    first = _begin(facts, "Лучше начать")[:-1] + f", и закончить в {end}."
    return _fit([*_must(first), *_minutes_compared(facts), *_may(clear_text(facts)), *_tail(facts)])


def _what_blocks(facts: BriefingFacts) -> str:
    if facts.verdict == "insufficient":
        return _insufficient(facts)
    if facts.verdict == "keep_requested":
        return _fit(
            [
                *_must(f"Рекомендуем оставить запрошенное окно {requested_span(facts)} UTC."),
                *_requested_minutes(facts, raised=True),
                *_may(clear_text(facts)),
                *_tail(facts, name_no_gaps=True),
            ]
        )
    return _fit(
        [*_must(_begin(facts)), *_minutes_compared(facts), *_may(clear_text(facts)), *_tail(facts, name_no_gaps=True)]
    )


_BUILDERS: dict[Question, Callable[[BriefingFacts], str]] = {
    "summary": _summary,
    "can_go_now": _can_go_now,
    "when_better": _when_better,
    "what_blocks": _what_blocks,
}


def render_template(facts: BriefingFacts) -> str:
    """Шаблонный текст сводки из тех же фактов: ответ сервиса, когда модели нет или её текст не прошёл проверку."""
    return _BUILDERS[facts.question](facts)
