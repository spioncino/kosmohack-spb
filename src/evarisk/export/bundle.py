import csv
import io
import json
import re
import zipfile

from evarisk.api.schemas import AssessmentResult
from evarisk.briefing.models import Briefing
from evarisk.briefing.service import build_briefing
from evarisk.replay.verify import Verification
from evarisk.types import Provenance, Recommendation, Window
from evarisk.windows.explain import LINE_NAMES, cap

BRIEFING_LLM = "сформулировано языковой моделью по фактам расчёта, числа проверены автоматически"
BRIEFING_TEMPLATE = "шаблон: модель недоступна"
BRIEFING_RULE = (
    "Модель формулирует, но не считает: каждое число и время текста сверено со списком фактов ниже, "
    "при несовпадении текст заменяется шаблонным."
)


# Строка по линии в объяснении начинается с названия линии и двоеточия: «Протоны: в запрошенном окне 4 ч 42 мин, …».
_PER_LINE = re.compile("^(?:" + "|".join(re.escape(cap(name)) for name in LINE_NAMES.values()) + "): ")


def _explanation_lines(rec: Recommendation) -> list[str]:
    """Объяснение обычными словами (D76) списком; строки по линиям под фразой с двоеточием идут вложенным списком."""
    out: list[str] = []
    nested = False
    for text in rec.explanation:
        nested = nested and bool(_PER_LINE.match(text))
        out.append(f"  - {text}" if nested else f"- {text}")
        nested = nested or text.endswith(":")
    return [*out, ""] if out else []


def _recommendation_line(rec: Recommendation) -> str:
    if rec.insufficient or rec.preferred is None:
        return "Оснований для выбора недостаточно. " + rec.reason
    return f"Предпочтительное окно {rec.preferred.start.isoformat()} – {rec.preferred.end.isoformat()}. {rec.reason}"


def _window_text(window: Window | None) -> str:
    return "оснований для выбора нет" if window is None else f"{window.start.isoformat()} – {window.end.isoformat()}"


def _minutes(value: float | None) -> str:
    return "не определена" if value is None else f"{value:.0f} мин"


def _per_line(total: float | None, by_line: dict[str, float]) -> str:
    """Минуты окна по линиям «sep_protons 40 мин, meteor_showers 0 мин» (D84): линии идут одновременно, сумма бывает
    больше окна. У сводок до D84 минут по линиям нет, и сумма называется с пометкой."""
    if by_line:
        return ", ".join(f"{line} {_minutes(value)}" for line, value in by_line.items())
    return f"{_minutes(total)} (сумма по линиям)"


def _exposure_lines(v: Verification) -> list[str]:
    """Строка отчёта об экспозиции экипажа; у расчётов без этих полей строки нет."""
    if v.exposure_hit_minutes is None or v.exposure_miss_minutes is None or v.exposure_false_alarm_minutes is None:
        return []
    return [
        f"- Экспозиция экипажа (минуты геомагнитного доступа с уровнем ≥ high). Попадания: "
        f"{v.exposure_hit_minutes:.0f} мин; пропуски: {v.exposure_miss_minutes:.0f} мин; ложные тревоги: "
        f"{v.exposure_false_alarm_minutes:.0f} мин."
    ]


BASELINES_INTRO = (
    "Все подходы видят только данные, опубликованные до отсечения, и сверяются с тем же фактом той же функцией. "
    "Бейзлайн заменяет линию SEP одним уровнем на период или на сутки и не знает траектории, поэтому окна внутри "
    "суток не различает. Главный столбец: минуты экспозиции экипажа по факту в окне, которое выбрал подход."
)


def _number(value: float | None) -> str:
    return "нет данных" if value is None else f"{value:.0f}"


def _counts(hit: float | None, miss: float | None, false_alarm: float | None) -> str:
    return f"{_number(hit)} / {_number(miss)} / {_number(false_alarm)}"


def _baselines_lines(v: Verification, preferred: Window | None) -> list[str]:
    """Таблица «наш расчёт против бейзлайнов»; у расчётов без бейзлайнов раздела нет."""
    if not v.baselines:
        return []
    lines = [
        "",
        "### Сравнение с простыми подходами",
        "",
        BASELINES_INTRO,
        "",
        "| подход | что предсказал | выбранное окно | экспозиция по факту в окне, мин | "
        "попадания / пропуски / ложные тревоги, мин экспозиции | заблаговременность | окно устояло |",
        "|---|---|---|---|---|---|---|",
        f"| наш расчёт | линия SEP поминутно по траектории | {_window_text(preferred)} | "
        f"{_number(v.preferred_exposure_minutes)} | "
        f"{_counts(v.exposure_hit_minutes, v.exposure_miss_minutes, v.exposure_false_alarm_minutes)} | "
        f"{_minutes(v.lead_time_minutes)} | {'да' if v.window_choice_stable else 'нет'} |",
    ]
    for b in v.baselines:
        lines.append(
            f"| {b.title} | {b.level}: {b.basis} | {_window_text(b.preferred)} | "
            f"{_number(b.preferred_exposure_minutes)} | "
            f"{_counts(b.exposure_hit_minutes, b.exposure_miss_minutes, b.exposure_false_alarm_minutes)} | "
            f"{_minutes(b.lead_time_minutes)} | {'да' if b.window_choice_stable else 'нет'} |"
        )
    lines += ["", f"В запрошенном окне по факту: {_number(v.requested_exposure_minutes)} мин экспозиции экипажа."]
    return lines


def _verification_section(v: Verification, preferred: Window | None = None) -> list[str]:
    first = "" if v.first_observed_alarm is None else f" (первый факт ≥ high {v.first_observed_alarm.isoformat()})"
    lines = [
        "",
        "## Проверка после события",
        "",
        "Считается отдельно, после рекомендации, и в расчёт и рекомендацию не входит (D41). Факт здесь это тот же "
        "расчёт с теми же правилами по всем данным, доступным сегодня, включая опубликованные позже отсечения. Это не "
        "независимые наблюдения: ошибки самих правил модели такая проверка не покажет. Постановка задачи требует "
        "сопоставить прогноз из прошлого с последующими наблюдениями, здесь они входят в факт через тот же расчёт.",
        "",
        f"Факт: тот же расчёт линии {v.line} и окон по всем доступным сегодня данным (AsOf без отсечения); "
        f"данные факта: {v.observed_availability}.",
        "",
        f"- Заблаговременность: {_minutes(v.lead_time_minutes)}{first}",
        *_exposure_lines(v),
        f"- Уровень ≥ high (обстановка и экспозиция вместе). Попадания: {v.hit_minutes:.0f} мин; "
        f"пропуски: {v.miss_minutes:.0f} мин; "
        f"ложные тревоги: {v.false_alarm_minutes:.0f} мин; спокойно у обоих: {v.quiet_minutes:.0f} мин; "
        f"без факта: {v.unverified_minutes:.0f} мин; прогноз без уровня: {v.predicted_unknown_minutes:.0f} мин.",
        f"- Рекомендация по факту: {_window_text(v.observed_preferred)}; "
        f"совпадает с прогнозом: {'да' if v.window_choice_stable else 'нет'}.",
        f"- Примечание: {v.note}",
        *_baselines_lines(v, preferred),
        "",
        "Интервалы факта:",
    ]
    if not v.observed:
        lines.append("- интервалов нет")
    for iv in v.observed:
        lines.append(
            f"- {iv.start.isoformat()} – {iv.end.isoformat()}: {iv.level} [{iv.kind}]; "
            f"источники: {', '.join(p.raw_id for p in iv.provenance)}"
        )
    return lines


VERIFICATION_ONLY = "только в проверке после события, в расчёт и рекомендацию не входит"


def _verification_provenance(v: Verification | None) -> dict[str, Provenance]:
    """Провенанс сверки: интервалы прогноза и факта и основания бейзлайнов."""
    if v is None:
        return {}
    found = {p.raw_id: p for iv in (*v.predicted, *v.observed) for p in iv.provenance}
    for b in v.baselines:
        for p in b.provenance:
            found.setdefault(p.raw_id, p)
    return found


def sources_of(r: AssessmentResult) -> list[Provenance]:
    """Источники расчёта плюс источники интервалов сверки, по одному на `raw_id`.

    Отчёт ссылается на `raw_id` интервалов `verification.observed`, `verification.predicted` и оснований
    бейзлайнов; наблюдения факта опубликованы позже отсечения, поэтому в `sources_used` расчёта их нет.
    """
    by_id = {p.raw_id: p for p in r.sources_used}
    for raw_id, p in _verification_provenance(r.verification).items():
        by_id.setdefault(raw_id, p)
    return list(by_id.values())


def _source_lines(r: AssessmentResult) -> list[str]:
    """Строки раздела «Источники»; источник, встречающийся только в сверке, помечен как не входящий в расчёт."""
    used = {p.raw_id for p in r.sources_used}
    lines = []
    for p in sources_of(r):
        mark = "" if p.raw_id in used else f" [{VERIFICATION_ONLY}]"
        lines.append(
            f"- {p.raw_id}: {p.source}/{p.product}, версия {p.data_version}, доступно с "
            f"{p.published_at.isoformat() if p.published_at else 'неизвестно'} ({p.published_at_kind}), "
            f"{p.url}{mark}"
        )
    return lines


def render_report(r: AssessmentResult) -> str:
    req = r.request
    lines = [
        f"# Оценка окна ВКД {r.id}",
        "",
        f"Режим: {req.mode}. Момент данных: {r.asof.isoformat() if r.asof else 'все доступные сегодня'}. "
        f"Версия алгоритма: {r.algorithm_version}. Создано: {r.created_at.isoformat()}.",
        "",
    ]
    if req.mode == "replay" and req.cutoff is not None:
        lines += [f"Отсечение информации: {req.cutoff.isoformat()}.", ""]
    lines += [
        f"Запрошенное окно: {req.eva_start.isoformat()} + {req.duration_hours} ч; период поиска "
        f"{req.search_start.isoformat()} – {req.search_end.isoformat()}.",
        "",
        "## Рекомендация",
        "",
        *_explanation_lines(r.recommendation),
        "Технические подробности: " + _recommendation_line(r.recommendation),
        "",
        "Правила:",
        *(f"- {x}" for x in r.recommendation.rules_applied),
    ]
    if r.geometry is not None:
        lines += ["", f"Геометрия доступа: {r.geometry.note}."]
    if r.verification is not None:
        lines += _verification_section(r.verification, r.recommendation.preferred)
    lines += [
        "",
        "## Траектория",
        "",
        f"{r.trajectory.provider}, эпоха {r.trajectory.epoch.isoformat()}, "
        f"{'реконструкция' if r.trajectory.is_reconstruction else 'прогноз на момент данных'}, "
        f"запись {r.trajectory.provenance.raw_id}.",
    ]
    for a in r.lines:
        lines += [
            "",
            f"## Линия {a.line} ({a.mechanism})",
            "",
            f"Данные: {a.availability}. Уверенность: {a.confidence.grade}, зависит от: {a.confidence.depends_on}.",
            "",
        ]
        for iv in a.intervals:
            lines.append(
                f"- {iv.start.isoformat()} – {iv.end.isoformat()}: {iv.level} [{iv.kind}]; "
                f"источники: {', '.join(p.raw_id for p in iv.provenance)}"
            )
        lines += ["", "Правило: " + (a.intervals[0].rule if a.intervals else "интервалов нет"), "", "Ограничения:"]
        lines += [f"- {x}" for x in a.limitations]
    lines += [
        "",
        "## Сравнение окон",
        "",
        "Часы даны по каждой линии штрафа отдельно в виде «high+critical / elevated / без уровня»; линии "
        "независимы, поэтому часы разных линий не складываются. Полнота это доля минут окна с известным уровнем, "
        "усреднённая по линиям.",
        "",
        "| начало | конец | часы по линиям | полнота | исключено |",
        "|---|---|---|---|---|",
    ]
    for s in r.windows:
        by_line = "; ".join(
            f"{name}: {h['high'] + h['critical']:.2f} / {h['elevated']:.2f} / {h['unknown']:.2f}"
            for name, h in s.per_line.items()
        )
        lines.append(
            f"| {s.window.start.isoformat()} | {s.window.end.isoformat()} | {by_line} | "
            f"{s.completeness:.0%} | {s.excluded_reason or ''} |"
        )
    lines += ["", "## Источники", ""]
    lines += _source_lines(r)
    lines += ["", "## Ограничения оценки", ""]
    lines += [f"- {x}" for x in r.limitations]
    return "\n".join(lines) + "\n"


def _fact_lines(b: Briefing) -> list[str]:
    f = b.facts
    lines = [
        f"- Вердикт: {f.verdict}",
        f"- Запрошенное окно: {f.requested.start.isoformat()} – {f.requested.end.isoformat()}, "
        f"минут high и critical по линиям: {_per_line(f.requested.exposure_minutes, f.requested.exposure_by_line)}; "
        f"минут без уровня по линиям: {_per_line(f.requested.unknown_minutes, f.requested.unknown_by_line)}",
    ]
    if f.recommended is not None:
        lines.append(
            f"- Рекомендованное окно: {f.recommended.start.isoformat()} – {f.recommended.end.isoformat()}, "
            f"минут high и critical по линиям: "
            f"{_per_line(f.recommended.exposure_minutes, f.recommended.exposure_by_line)}"
        )
    for item in f.exposed:
        lines.append(
            f"- Отрезок {item.start.isoformat()} – {item.end.isoformat()}: {item.line} {item.level}, "
            f"{_minutes(item.minutes)}"
        )
    if f.clear_after is not None:
        lines.append(f"- После {f.clear_after.isoformat()} минут high и critical в периоде поиска нет")
    for gap in f.gaps:
        lines.append(f"- Пробел данных: линия {gap.line} ({gap.availability})")
    if f.disabled_sources:
        lines.append(f"- Источники отключены пользователем: {', '.join(f.disabled_sources)}")
    if f.confidence_grade is not None:
        lines.append(f"- Уверенность линии протонов: {f.confidence_grade}, зависит от: {f.confidence_depends_on}")
    if f.marginal_note is not None:
        lines.append(f"- {f.marginal_note}")
    lines.append(f"- Окон исключено: {f.windows_excluded}, кандидатов: {f.windows_total}")
    lines.append(f"- Основание данных: {f.basis}")
    return lines


def render_briefing(b: Briefing) -> str:
    """Сводка для экипажа в архиве: текст, происхождение текста, факты и провенанс под ними."""
    origin = BRIEFING_LLM if b.source == "llm" else BRIEFING_TEMPLATE
    model = f" Модель: {b.model}." if b.source == "llm" and b.model else ""
    lines = [
        f"# Сводка для экипажа {b.assessment_id}",
        "",
        f"Вопрос: {b.question}. Язык: {b.language}. Сформировано: {b.generated_at.isoformat()}. "
        f"Версия алгоритма: {b.algorithm_version}.",
        "",
        b.text,
        "",
        f"Происхождение текста: {origin}.{model} Проверка чисел: {'пройдена' if b.validated else 'не пройдена'}.",
        "",
        BRIEFING_RULE,
    ]
    if b.violations:
        lines += ["", "Нарушения, из-за которых текст модели не принят:"]
        lines += [f"- {v}" for v in b.violations]
    lines += ["", "## Факты", ""]
    lines += _fact_lines(b)
    lines += ["", f"Разрешённые числа, времена и даты: {', '.join(b.facts.allowed_values)}.", "", "## Источники", ""]
    for p in b.facts.provenance:
        lines.append(f"- {p.raw_id}: {p.source}/{p.product}, версия {p.data_version}, {p.url}")
    lines += ["", "## Текст для чтения вслух", "", b.speech_text]
    return "\n".join(lines) + "\n"


def build_zip(r: AssessmentResult, briefing: Briefing | None = None) -> bytes:
    """Архив по сохранённому расчёту; без готовой сводки в архив кладётся шаблонная, собранная из фактов."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("result.json", r.model_dump_json(indent=1))
        z.writestr("report.md", render_report(r))
        z.writestr("briefing.md", render_briefing(briefing or build_briefing(r)))
        z.writestr(
            "sources.json",
            json.dumps([p.model_dump(mode="json") for p in sources_of(r)], ensure_ascii=False, indent=1),
        )
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        w.writerow(["line", "start", "end", "level", "kind", "rule"])
        for a in r.lines:
            for iv in a.intervals:
                w.writerow([a.line, iv.start.isoformat(), iv.end.isoformat(), iv.level, iv.kind, iv.rule])
        z.writestr("timeline.csv", out.getvalue())
    return buf.getvalue()
