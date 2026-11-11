from collections.abc import Sequence
from pathlib import Path
from urllib.parse import quote

from evarisk.api.schemas import AssessmentResult
from evarisk.briefing.ask import ask_context
from evarisk.briefing.facts import build_facts
from evarisk.briefing.llm import LlmClient, Message, ask_messages, messages_for, model_name, retry_messages
from evarisk.briefing.models import Briefing, BriefingFacts, Language, Question, TextSource
from evarisk.briefing.speech import FALLBACK_SPEECH, SpeechNotSpeakable, to_speech
from evarisk.briefing.templates import STYLE, render_template
from evarisk.briefing.tts import NoSynthesizer, SpeechSynthesizer
from evarisk.briefing.validate import validate
from evarisk.config import Settings
from evarisk.timeutil import utc_now

MODEL_FAILED_PREFIX = "модель не ответила"
MODEL_FAILED = MODEL_FAILED_PREFIX + ": {error}"
SPEECH_FROM_TEMPLATE = "речевой текст собран по шаблону: в тексте модели остались непроизносимые знаки"
ERROR_CHARS = 200


def cache_path(settings: Settings, assessment_id: str, question: Question, language: Language) -> Path:
    return settings.assessments_root / f"{assessment_id}.briefing.{question}.{language}.json"


def audio_path(settings: Settings, assessment_id: str, question: Question, language: Language) -> Path:
    return settings.assessments_root / f"{assessment_id}.briefing.{question}.{language}.wav"


def audio_url(assessment_id: str, question: Question, language: Language) -> str:
    return f"/assessments/{quote(assessment_id)}/briefing/audio?question={question}&language={language}"


def load_cached(
    settings: Settings, assessment_id: str, question: Question, language: Language = "ru"
) -> Briefing | None:
    path = cache_path(settings, assessment_id, question, language)
    if not path.exists():
        return None
    return Briefing.model_validate_json(path.read_text(encoding="utf-8"))


def save(briefing: Briefing, settings: Settings) -> None:
    settings.assessments_root.mkdir(parents=True, exist_ok=True)
    path = cache_path(settings, briefing.assessment_id, briefing.question, briefing.language)
    path.write_text(briefing.model_dump_json(indent=1), encoding="utf-8")


ASK_MAX_CHARS = 300
ASK_NO_MODEL = "модель не подключена: свой вопрос остаётся без ответа, показана общая сводка по шаблону"
ASK_NOT_VALIDATED = "ответ модели на свой вопрос не прошёл проверку чисел и обязательных фраз, показана общая сводка"
# Непроверенного ответа нет (D89): сводка после этой фразы не выдаётся за ответ на вопрос.
NOT_ANSWERED = "На этот вопрос проверенного ответа нет. Общая сводка."
HISTORY_TURNS = 3
HISTORY_MAX_CHARS = 1000
_CONTROL = dict.fromkeys(range(32), " ")


def model_failed(violations: Sequence[str]) -> bool:
    """Модель не ответила: текста от неё не было, поэтому проверку чисел он не проходил и не отвергался."""
    return any(v.startswith(MODEL_FAILED_PREFIX) for v in violations)


def _clean_text(text: str) -> str:
    return " ".join(text.translate(_CONTROL).replace("«", '"').replace("»", '"').split())


def clean_question(asked: str) -> str:
    """Текст вопроса без управляющих знаков и лишних пробелов, не длиннее `ASK_MAX_CHARS`."""
    return _clean_text(asked)[:ASK_MAX_CHARS]


def _fallback(facts: BriefingFacts, ask: bool) -> str:
    """Шаблон сводки; на свой вопрос перед ним фраза о том, что ответа нет (D89)."""
    return f"{NOT_ANSWERED} {render_template(facts)}" if ask else render_template(facts)


def _phrase(
    facts: BriefingFacts, llm: LlmClient | None, messages: list[Message] | None = None, *, ask: bool = False
) -> tuple[str, TextSource, str | None, list[str]]:
    """Текст сводки: модель формулирует, проверка чисел решает; после второго отказа отвечает шаблон."""
    if llm is None:
        return _fallback(facts, ask), "template", None, []
    messages = messages_for(facts) if messages is None else messages
    try:
        # Модель ставит каждую фразу с новой строки; сводка это один абзац для чтения вслух.
        answer = " ".join(llm.complete(messages).split())
    except Exception as exc:
        return _fallback(facts, ask), "template", llm.model, [MODEL_FAILED.format(error=_error_text(exc))]
    violations = validate(answer, facts, ask=ask)
    if not violations:
        return answer.strip(), "llm", llm.model, []
    try:
        second = " ".join(llm.complete(retry_messages(messages, answer, violations, ask=ask)).split())
    except Exception as exc:
        return _fallback(facts, ask), "template", llm.model, [MODEL_FAILED.format(error=_error_text(exc))]
    repeated = validate(second, facts, ask=ask)
    if not repeated:
        return second.strip(), "llm", llm.model, []
    return _fallback(facts, ask), "template", llm.model, repeated


def _error_text(exc: Exception) -> str:
    """Причина отказа модели без содержимого запроса: класс исключения и начало его сообщения."""
    return f"{type(exc).__name__}: {str(exc)[:ERROR_CHARS]}"


def _speech(text: str, facts: BriefingFacts) -> tuple[str, str | None]:
    """Речевой текст из проверенного текста; при непроизносимом остатке берётся шаблон тех же фактов."""
    for candidate, note in ((text, None), (render_template(facts), SPEECH_FROM_TEMPLATE)):
        try:
            return to_speech(candidate), note
        except SpeechNotSpeakable:
            continue
    return FALLBACK_SPEECH, SPEECH_FROM_TEMPLATE


def build_briefing(
    result: AssessmentResult, question: Question = "summary", *, language: Language = "ru", llm: LlmClient | None = None
) -> Briefing:
    """Сводка по сохранённому расчёту: факты, текст, речевой вариант; звук добавляется отдельно."""
    facts = build_facts(result, question)
    text, source, model, violations = _phrase(facts, llm)
    speech, speech_note = _speech(text, facts)
    if speech_note is not None:
        violations = [*violations, speech_note]
    return Briefing(
        assessment_id=result.id,
        question=question,
        language=language,
        text=text,
        speech_text=speech,
        source=source,
        model=model_name(model),
        validated=not validate(text, facts),
        violations=tuple(violations),
        facts=facts,
        generated_at=utc_now(),
        algorithm_version=result.algorithm_version,
        audio_url=None,
        style=STYLE,
    )


def clean_history(history: Sequence[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """Последние `HISTORY_TURNS` пар «вопрос, ответ» без управляющих знаков, каждая часть обрезана."""
    turns = [(clean_question(q), _clean_text(a)[:HISTORY_MAX_CHARS]) for q, a in history]
    return tuple((q, a) for q, a in turns if q and a)[-HISTORY_TURNS:]


def answer_question(
    result: AssessmentResult,
    asked: str,
    *,
    language: Language = "ru",
    llm: LlmClient | None = None,
    history: Sequence[tuple[str, str]] = (),
) -> Briefing:
    """Ответ на свой вопрос экипажа: факты сводки и факты вопроса (D89), своя проверка; в кеш не пишется.

    Без модели и при тексте, не прошедшем проверку, текст начинается с `NOT_ANSWERED`, дальше общая сводка по
    шаблону с названной причиной: непроверенный текст модели не показывается, а сводка не выдаётся за ответ. Отказ
    самой модели и отвергнутый текст это разные причины: первой стоит та, которая была на деле.
    """
    question = clean_question(asked)
    context = ask_context(result, question)
    facts = context.facts
    messages = None if llm is None else ask_messages(facts, question, context.lines, clean_history(history))
    text, source, model, violations = _phrase(facts, llm, messages, ask=True)
    if llm is None:
        violations = [ASK_NO_MODEL]
    elif source == "template" and not model_failed(violations):
        violations = [ASK_NOT_VALIDATED, *violations]
    speech, speech_note = _speech(text, facts)
    if speech_note is not None:
        violations = [*violations, speech_note]
    return Briefing(
        assessment_id=result.id,
        question="summary",
        language=language,
        text=text,
        speech_text=speech,
        source=source,
        model=model_name(model),
        validated=not validate(text, facts, ask=True),
        violations=tuple(violations),
        facts=facts,
        generated_at=utc_now(),
        algorithm_version=result.algorithm_version,
        audio_url=None,
        style=STYLE,
        asked=question,
    )


def _with_audio(
    briefing: Briefing, settings: Settings, synthesizer: SpeechSynthesizer, refresh: bool = False
) -> Briefing:
    path = audio_path(settings, briefing.assessment_id, briefing.question, briefing.language)
    url = audio_url(briefing.assessment_id, briefing.question, briefing.language)
    if path.exists() and not refresh:
        return briefing.model_copy(update={"audio_url": url, "voice_error": None})
    synthesized = synthesizer.synthesize(briefing.speech_text, path)
    return briefing.model_copy(
        update={"audio_url": url if synthesized.path is not None else None, "voice_error": synthesized.error}
    )


def briefing_for(
    result: AssessmentResult,
    question: Question,
    settings: Settings,
    *,
    language: Language = "ru",
    llm: LlmClient | None = None,
    synthesizer: SpeechSynthesizer | None = None,
    voice: bool = False,
    refresh: bool = False,
) -> Briefing:
    """Готовая сводка: из кеша рядом с расчётом или заново; `refresh` пересобирает и текст, и звук."""
    cached = None if refresh else load_cached(settings, result.id, question, language)
    if cached is not None and cached.style < STYLE:
        # Сводка старого стиля (телеграфный текст до D77, без минут elevated до D75) собирается заново.
        cached = None
    if cached is not None and llm is not None and cached.source == "template" and model_failed(cached.violations):
        # Отказ модели не кешируется навсегда: при подключённой модели сводка собирается заново.
        cached = None
    briefing = cached if cached is not None else build_briefing(result, question, language=language, llm=llm)
    if voice:
        briefing = _with_audio(briefing, settings, synthesizer or NoSynthesizer(), refresh=refresh)
    if briefing != cached:
        save(briefing, settings)
    return briefing
