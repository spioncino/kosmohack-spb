import io
import json
import zipfile
from pathlib import Path

from evarisk.api.schemas import AssessmentResult
from evarisk.briefing.service import build_briefing
from evarisk.export.bundle import build_zip, render_report


def load_example() -> AssessmentResult:
    path = sorted(Path("tests/fixtures/assessments").glob("*.json"))[0]
    return AssessmentResult.model_validate_json(path.read_text(encoding="utf-8"))


def test_zip_contains_consistent_files() -> None:
    result = load_example()
    z = zipfile.ZipFile(io.BytesIO(build_zip(result)))
    assert set(z.namelist()) == {"result.json", "report.md", "sources.json", "timeline.csv", "briefing.md"}
    assert json.loads(z.read("result.json")) == json.loads(result.model_dump_json())
    sources = json.loads(z.read("sources.json"))
    assert {s["raw_id"] for s in sources} == {p.raw_id for p in result.sources_used}
    csv_lines = z.read("timeline.csv").decode().splitlines()
    assert csv_lines[0] == "line,start,end,level,kind,rule"
    assert len(csv_lines) == 1 + sum(len(a.intervals) for a in result.lines)


def test_briefing_without_a_ready_one_is_the_template_and_names_its_origin() -> None:
    result = load_example()
    z = zipfile.ZipFile(io.BytesIO(build_zip(result)))
    text = z.read("briefing.md").decode()
    assert "шаблон: модель недоступна" in text
    assert "Решение за руководителем работ." in text
    assert "## Факты" in text
    for p in result.sources_used:
        assert p.raw_id in text


def test_briefing_of_the_model_is_marked_as_checked_numbers() -> None:
    result = load_example()
    briefing = build_briefing(result, "summary").model_copy(update={"source": "llm", "model": "qwen-test"})
    text = zipfile.ZipFile(io.BytesIO(build_zip(result, briefing))).read("briefing.md").decode()
    assert "сформулировано языковой моделью по фактам расчёта, числа проверены автоматически" in text
    assert "qwen-test" in text


def test_report_mentions_recommendation_cutoff_and_every_source() -> None:
    result = load_example()
    text = render_report(result)
    assert result.algorithm_version in text
    assert ("Отсечение" in text) == (result.request.mode == "replay")
    for p in result.sources_used:
        assert p.raw_id in text


def test_report_puts_the_plain_explanation_before_the_technical_reason() -> None:
    result = load_example()
    explanation = (
        "Рекомендуем перенести выход на 10:45–16:45 UTC, на 2 ч 15 мин раньше.",
        "Опасных минут нет ни в запрошенном, ни в новом окне.",
        "Зато в новом окне меньше повышенного фона:",
        "Протоны: в запрошенном окне 4 ч 42 мин, в новом 2 ч 27 мин. Протонов больше обычного.",
        "Метеоры: в запрошенном окне нет, в новом 30 мин. Частиц больше обычного.",
        "Среди окон, где опасность наименьшая, это ближайшее к запрошенному.",
    )
    rec = result.recommendation.model_copy(update={"explanation": explanation})
    text = render_report(result.model_copy(update={"recommendation": rec}))
    # Строки по линиям (D78: «Протоны: в запрошенном окне …») вложены под фразой с двоеточием.
    assert (
        "- Рекомендуем перенести выход на 10:45–16:45 UTC, на 2 ч 15 мин раньше.\n"
        "- Опасных минут нет ни в запрошенном, ни в новом окне.\n"
        "- Зато в новом окне меньше повышенного фона:\n"
        "  - Протоны: в запрошенном окне 4 ч 42 мин, в новом 2 ч 27 мин. Протонов больше обычного.\n"
        "  - Метеоры: в запрошенном окне нет, в новом 30 мин. Частиц больше обычного.\n"
        "- Среди окон, где опасность наименьшая, это ближайшее к запрошенному.\n"
    ) in text
    assert text.index(explanation[0]) < text.index("Технические подробности: ") < text.index(rec.reason)
    # Расчёт до D76 без объяснения: остаётся только технический текст.
    old = result.recommendation.model_copy(update={"explanation": ()})
    assert "Технические подробности: " in render_report(result.model_copy(update={"recommendation": old}))
