import re
from datetime import datetime

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import Engine
from starlette.middleware.base import RequestResponseEndpoint

from evarisk.api.schemas import (
    AssessmentRequest,
    JobCreated,
    JobStatus,
    SourceInfo,
    SourceStateUpdate,
    TrajectoryMode,
    TrajectoryQuery,
)
from evarisk.api.service import (
    ASSESSMENT_ID,
    NoTrajectoryData,
    build_trajectory,
    iso_z,
    latest_conjunctions,
    latest_orbit,
    list_assessments,
    load_assessment,
    manual_index,
    manual_path,
    rules_snapshot,
    run_assessment,
)
from evarisk.briefing.llm import LlmClient, make_client
from evarisk.briefing.models import LANGUAGES, QUESTIONS, Language, Question
from evarisk.briefing.service import (
    ASK_MAX_CHARS,
    HISTORY_MAX_CHARS,
    HISTORY_TURNS,
    answer_question,
    audio_path,
    briefing_for,
    clean_question,
    load_cached,
)
from evarisk.briefing.tts import SpeechSynthesizer, make_synthesizer
from evarisk.config import Settings
from evarisk.db.engine import init_schema, make_engine
from evarisk.db.jobs import JobsRepo
from evarisk.db.source_state import WORKER_SOURCE, SourceStateRepo
from evarisk.export.bundle import build_zip
from evarisk.sources.base import SourceAdapter
from evarisk.sources.registry import adapters
from evarisk.store.raw import RawStore
from evarisk.timeutil import utc_now

TEXT_TYPES = ("text/", "application/json", "application/xml")
TEXT_LIMIT_BYTES = 2_000_000
SYNC_ASSESSMENT_SUMMARY = "Синхронный вариант: расчёт в запросе. Для прогресса и фоновой очереди POST /jobs."
NO_CURRENT_ASSESSMENT = "расчётов в режиме «сейчас» нет: внешний терминал получит сводку после первого такого расчёта"
NO_AUDIO = "озвучки для этой сводки нет: запросите сводку с voice=true при настроенной службе синтеза"
WAV_TYPE = "audio/wav"


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _validation_message(exc: RequestValidationError | ValidationError) -> str:
    return "; ".join(str(e.get("msg")) for e in exc.errors())


def _mount_static(app: FastAPI, settings: Settings) -> None:
    """Сборка фронтенда на «/» и старая страница web/ на «/dev», если сборка есть; иначе web/ на «/».

    Вызывается после объявления всех маршрутов API: Mount("/") перехватывает любой путь, объявленный после него.
    """
    web = StaticFiles(directory="web", html=True)
    if settings.frontend_dist.is_dir():
        app.mount("/dev", web, name="web")
        app.mount("/", StaticFiles(directory=str(settings.frontend_dist), html=True), name="frontend")
    else:
        app.mount("/", web, name="web")


def _json(model: JobCreated | JobStatus | SourceInfo, status: int = 200) -> Response:
    return Response(content=model.model_dump_json(), media_type="application/json", status_code=status)


def _source_info(name: str, adapter: SourceAdapter, states: SourceStateRepo, store: RawStore) -> SourceInfo:
    records = store.records(name)
    return SourceInfo.build(
        name,
        products=adapter.products,
        cadence_s=adapter.cadence.total_seconds(),
        supports_archive=adapter.supports_archive,
        state=states.get(name),
        last_record_fetched_at=max((r.fetched_at for r in records), default=None),
        records=len(records),
    )


def _archive_source_info(name: str, states: SourceStateRepo, store: RawStore) -> SourceInfo:
    """Источник без адаптера: записи импортированы из зеркала архивов, обновления нет, отключить можно."""
    records = store.records(name)
    return SourceInfo.build(
        name,
        products=tuple(sorted({r.product for r in records})),
        cadence_s=None,
        archive_only=True,
        supports_archive=True,
        state=states.get(name),
        last_record_fetched_at=max((r.fetched_at for r in records), default=None),
        records=len(records),
    )


def default_llm() -> LlmClient | None:
    """Клиент модели по окружению; любая ошибка создания это работа без модели, а не отказ сервиса."""
    try:
        return make_client()
    except Exception as exc:
        print(f"клиент модели не создан, сводка будет шаблонной: {type(exc).__name__}: {exc}")
        return None


class BriefingTurn(BaseModel):
    """Предыдущий вопрос экипажа и ответ на него: контекст следующего вопроса (D89)."""

    asked: str = Field(max_length=ASK_MAX_CHARS)
    answer: str = Field(max_length=HISTORY_MAX_CHARS)


class BriefingAsk(BaseModel):
    """Тело запроса со своим вопросом экипажа и до трёх предыдущих пар «вопрос, ответ»."""

    text: str = Field(min_length=1, max_length=ASK_MAX_CHARS)
    language: str = "ru"
    history: list[BriefingTurn] = Field(default_factory=list, max_length=HISTORY_TURNS)


MAX_BODY_BYTES = 262_144
TOO_LARGE = (
    "тело запроса больше {limit} байт: сервис открыт через публичный туннель и принимает только небольшие запросы"
)
CLOUDFLARE_HEADERS = ("cf-connecting-ip", "cf-ray")
SOURCE_STATE_PATH = re.compile(r"^/sources/[^/]+/(state|refresh)/?$")
TUNNEL_READONLY = (
    "запрос пришёл через публичный туннель Cloudflare: состояние источников и их обновление через туннель не "
    "меняются, эти действия выполняются с самой машины сервиса. Отключается настройкой EVARISK_TUNNEL_READONLY"
)


def _declared_length(request: Request) -> int | None:
    """Длина тела из заголовка Content-Length; заголовка нет или он не число это `None`."""
    value = request.headers.get("content-length")
    if value is None or not value.isdigit():
        return None
    return int(value)


def _through_cloudflare(request: Request) -> bool:
    """Заголовки, которые Cloudflare ставит сам: запрос пришёл через туннель, а не с машины сервиса."""
    return any(h in request.headers for h in CLOUDFLARE_HEADERS)


SYNC_NO_BACKFILL = (
    "; синхронный расчёт архив не докачивает: задание через POST /jobs докачает архив периода из открытых "
    "источников и посчитает (D59)"
)


def create_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    registry: dict[str, SourceAdapter] | None = None,
    llm: LlmClient | None = None,
    synthesizer: SpeechSynthesizer | None = None,
) -> FastAPI:
    settings = settings or Settings()
    speaker = synthesizer or make_synthesizer(settings.tts_url)
    engine = engine or make_engine(settings.database_url)
    init_schema(engine)
    jobs_repo = JobsRepo(engine)
    states = SourceStateRepo(engine)
    sources = adapters() if registry is None else registry
    store = RawStore(settings.raw_root)
    app = FastAPI(title="evarisk", version="0.1.0")
    app.state.engine = engine

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error(400, "validation", _validation_message(exc))

    @app.middleware("http")
    async def _guard(request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Ограничения публичного доступа: размер тела и запрет менять состояние источников через туннель."""
        declared = _declared_length(request)
        if declared is not None and declared > MAX_BODY_BYTES:
            return _error(413, "too_large", TOO_LARGE.format(limit=MAX_BODY_BYTES))
        through_tunnel = settings.tunnel_readonly and _through_cloudflare(request)
        if through_tunnel and request.method == "POST" and SOURCE_STATE_PATH.match(request.url.path):
            return _error(403, "tunnel_readonly", TUNNEL_READONLY)
        return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, str | None]:
        tick = states.get(WORKER_SOURCE).last_attempt_at
        return {
            "status": "ok",
            "raw_root": str(settings.raw_root),
            "worker_last_tick": None if tick is None else iso_z(tick),
        }

    @app.post("/assessments", summary=SYNC_ASSESSMENT_SUMMARY)
    def create_assessment(req: AssessmentRequest) -> Response:
        try:
            result = run_assessment(req, store, settings, disabled=states.disabled_sources())
        except NoTrajectoryData as exc:
            hint = SYNC_NO_BACKFILL if req.mode != "current" and settings.archive_backfill else ""
            return _error(409, "no_trajectory", f"{exc}{hint}")
        except ValidationError as exc:
            return _error(400, "validation", str(exc))
        return Response(content=result.model_dump_json(), media_type="application/json")

    @app.get("/assessments")
    def assessments() -> list[dict[str, str]]:
        return list_assessments(settings)

    @app.get("/assessments/{assessment_id}")
    def get_assessment(assessment_id: str) -> Response:
        result = load_assessment(assessment_id, settings)
        if result is None:
            return _error(404, "not_found", "расчёт не найден")
        return Response(content=result.model_dump_json(), media_type="application/json")

    def _question(value: str) -> Question | JSONResponse:
        if value not in QUESTIONS:
            return _error(400, "unknown_question", f"вопрос {value!r} неизвестен; допустимы: {', '.join(QUESTIONS)}")
        return value

    def _language(value: str) -> Language | JSONResponse:
        if value not in LANGUAGES:
            return _error(400, "unknown_language", f"язык {value!r} не поддержан; допустимы: {', '.join(LANGUAGES)}")
        return value

    def _briefing_response(result_id: str, question: str, language: str, voice: bool, refresh: bool) -> Response:
        asked = _question(question)
        if isinstance(asked, JSONResponse):
            return asked
        tongue = _language(language)
        if isinstance(tongue, JSONResponse):
            return tongue
        result = load_assessment(result_id, settings)
        if result is None:
            return _error(404, "not_found", "расчёт не найден")
        briefing = briefing_for(
            result,
            asked,
            settings,
            language=tongue,
            llm=llm,
            synthesizer=speaker,
            voice=voice,
            refresh=refresh,
        )
        return Response(content=briefing.model_dump_json(), media_type="application/json")

    @app.get("/assessments/{assessment_id}/briefing")
    def assessment_briefing(
        assessment_id: str,
        question: str = "summary",
        language: str = "ru",
        voice: bool = False,
        refresh: bool = False,
    ) -> Response:
        return _briefing_response(assessment_id, question, language, voice, refresh)

    @app.post("/assessments/{assessment_id}/briefing/ask")
    def briefing_ask(assessment_id: str, body: BriefingAsk) -> Response:
        """Свой вопрос экипажа по расчёту: ответ проходит ту же проверку чисел, что и сводка; в кеш не пишется."""
        tongue = _language(body.language)
        if isinstance(tongue, JSONResponse):
            return tongue
        if not clean_question(body.text):
            return _error(400, "empty_question", "текст вопроса пуст")
        result = load_assessment(assessment_id, settings)
        if result is None:
            return _error(404, "not_found", "расчёт не найден")
        history = [(turn.asked, turn.answer) for turn in body.history]
        answer = answer_question(result, body.text, language=tongue, llm=llm, history=history)
        return Response(content=answer.model_dump_json(), media_type="application/json")

    @app.get("/assessments/{assessment_id}/briefing/audio")
    def briefing_audio(assessment_id: str, question: str = "summary", language: str = "ru") -> Response:
        asked = _question(question)
        if isinstance(asked, JSONResponse):
            return asked
        tongue = _language(language)
        if isinstance(tongue, JSONResponse):
            return tongue
        if ASSESSMENT_ID.match(assessment_id) is None:
            return _error(404, "not_found", NO_AUDIO)
        path = audio_path(settings, assessment_id, asked, tongue)
        if not path.exists():
            return _error(404, "not_found", NO_AUDIO)
        return Response(content=path.read_bytes(), media_type=WAV_TYPE)

    @app.get("/briefing/latest")
    def briefing_latest(voice: bool = False, language: str = "ru", refresh: bool = False) -> Response:
        current = [item for item in list_assessments(settings) if item["mode"] == "current"]
        if not current:
            return _error(404, "not_found", NO_CURRENT_ASSESSMENT)
        return _briefing_response(current[0]["id"], "summary", language, voice, refresh)

    @app.get("/assessments/{assessment_id}/export")
    def export_assessment(assessment_id: str) -> Response:
        result = load_assessment(assessment_id, settings)
        if result is None:
            return _error(404, "not_found", "расчёт не найден")
        return Response(
            content=build_zip(result, load_cached(settings, assessment_id, "summary")),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="evarisk_{assessment_id}.zip"'},
        )

    @app.post("/jobs", status_code=202)
    def create_job(req: AssessmentRequest) -> Response:
        job = jobs_repo.create("assessment", req.model_dump(mode="json"), utc_now())
        return _json(JobCreated(job_id=job.id), 202)

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str) -> Response:
        job = jobs_repo.get(job_id)
        if job is None:
            return _error(404, "not_found", "задание не найдено")
        return _json(JobStatus.from_job(job))

    @app.get("/sources")
    def list_sources() -> Response:
        infos = [_source_info(name, adapter, states, store) for name, adapter in sources.items()]
        archive_names = sorted({r.source for r in store.records()} - set(sources))
        infos += [_archive_source_info(name, states, store) for name in archive_names]
        return Response(content=f"[{','.join(i.model_dump_json() for i in infos)}]", media_type="application/json")

    @app.post("/sources/{name}/state")
    def set_source_state(name: str, body: SourceStateUpdate) -> Response:
        adapter = sources.get(name)
        if adapter is None and not store.records(name):
            return _error(404, "not_found", f"источник {name!r} не зарегистрирован и записей в хранилище не имеет")
        states.set_state(name, body.state, body.reason, utc_now())
        if adapter is None:
            return _json(_archive_source_info(name, states, store))
        return _json(_source_info(name, adapter, states, store))

    @app.post("/sources/{name}/refresh", status_code=202)
    def refresh_source(name: str) -> Response:
        if name not in sources:
            if store.records(name):
                return _error(409, "archive_only", f"источник {name!r} архивный: адаптера обновления нет")
            return _error(404, "not_found", f"источник {name!r} не зарегистрирован")
        job = jobs_repo.create("refresh", {"source": name}, utc_now())
        return _json(JobCreated(job_id=job.id), 202)

    @app.get("/evidence/{raw_id}")
    def evidence(raw_id: str) -> Response:
        try:
            record = store.get(raw_id)
        except KeyError:
            return _error(404, "not_found", "сырая запись не найдена")
        text = None
        if record.size <= TEXT_LIMIT_BYTES and record.content_type.startswith(TEXT_TYPES):
            text = store.read(raw_id).decode("utf-8", errors="replace")
        payload = {
            "record": record.model_dump(mode="json"),
            "content_text": text,
            "download_url": f"/evidence/{raw_id}/download",
        }
        return JSONResponse(content=payload)

    @app.get("/evidence/{raw_id}/download")
    def evidence_download(raw_id: str) -> Response:
        try:
            record = store.get(raw_id)
        except KeyError:
            return _error(404, "not_found", "сырая запись не найдена")
        return Response(content=store.read(raw_id), media_type=record.content_type)

    @app.get("/trajectory")
    def trajectory(
        start: datetime,
        end: datetime,
        step_minutes: int = Query(default=1, ge=1, le=10),
        mode: TrajectoryMode = "current",
    ) -> Response:
        try:
            query = TrajectoryQuery(start=start, end=end, step_minutes=step_minutes, mode=mode)
        except ValidationError as exc:
            return _error(400, "validation", _validation_message(exc))
        try:
            result = build_trajectory(query, store, disabled=states.disabled_sources())
        except NoTrajectoryData as exc:
            return _error(409, "no_trajectory", str(exc))
        return Response(content=result.model_dump_json(), media_type="application/json")

    @app.get("/orbit/latest")
    def orbit_latest() -> Response:
        try:
            result = latest_orbit(store)
        except ValueError as exc:
            return _error(500, "bad_record", f"последняя запись celestrak/gp не разобрана: {exc}")
        return Response(content=result.model_dump_json(), media_type="application/json")

    @app.get("/conjunctions/latest")
    def conjunctions_latest() -> Response:
        try:
            result = latest_conjunctions(store, utc_now())
        except ValueError as exc:
            return _error(500, "bad_record", f"последний отчёт SOCRATES не разобран: {exc}")
        return Response(content=result.model_dump_json(), media_type="application/json")

    @app.get("/manual")
    def manual() -> Response:
        entries = [e.model_dump() for e in manual_index(settings.manual_root)]
        return JSONResponse(entries)

    @app.get("/manual/{name:path}")
    def manual_file(name: str) -> Response:
        path = manual_path(settings.manual_root, name)
        if path is None:
            return _error(404, "not_found", f"документа {name} нет")
        return Response(content=path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")

    @app.get("/rules")
    def rules() -> Response:
        return Response(content=rules_snapshot().model_dump_json(), media_type="application/json")

    _mount_static(app, settings)
    return app


app = create_app(llm=default_llm())
