"""Локальный сервис синтеза речи для сводки экипажу: Silero TTS v4_ru на CPU, без облака.

Запуск: tts/.venv/bin/uvicorn server:app --app-dir tts --host 127.0.0.1 --port 8010
Страница проверки на слух: GET /.
Контракт: POST /synthesize {"text", "language": "ru", "speaker", "rate"} -> audio/wav; GET /health.
Модель не произносит цифры и латиницу и пропускает их молча, поэтому такой текст отвергается с 422:
вызывающая сторона передаёт текст, записанный словами.
"""

import io
import logging
import os
import re
import urllib.request
import wave
from xml.sax.saxutils import escape
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import numpy as np
import torch
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

MODEL_PATH = Path(os.getenv("TTS_MODEL_PATH", "data/tts/v4_ru.pt"))
MODEL_URL = os.getenv("TTS_MODEL_URL", "https://models.silero.ai/models/tts/ru/v4_ru.pt")
DEFAULT_SPEAKER = os.getenv("TTS_SPEAKER", "kseniya")
SAMPLE_RATE = int(os.getenv("TTS_SAMPLE_RATE", "48000"))
THREADS = int(os.getenv("TTS_THREADS", "4"))
DEFAULT_RATE = os.getenv("TTS_RATE", "fast")
RATES = ("slow", "medium", "fast", "x-fast")
MAX_CHARS = 2000
CHUNK_CHARS = 800
PAUSE_SECONDS = 0.25
ENGINE = "silero v4_ru"
PAGE = Path(__file__).with_name("page.html")
UNSPEAKABLE = re.compile(r"[0-9A-Za-z]")
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")

log = logging.getLogger("tts")
state: dict[str, Any] = {}


class SynthesisRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_CHARS)
    language: str = "ru"
    speaker: str | None = None
    rate: str | None = None


def load_model() -> Any:
    if not MODEL_PATH.exists():
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        log.warning("модель %s не найдена, загрузка из %s", MODEL_PATH, MODEL_URL)
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)  # noqa: S310
    torch.set_num_threads(THREADS)
    model = torch.package.PackageImporter(str(MODEL_PATH)).load_pickle("tts_models", "model")
    model.to(torch.device("cpu"))
    return model


def chunks(text: str) -> list[str]:
    """Текст по предложениям кусками не длиннее CHUNK_CHARS: у модели есть предел длины одного вызова."""
    parts: list[str] = []
    current = ""
    for sentence in SENTENCE_END.split(text.strip()):
        if current and len(current) + 1 + len(sentence) > CHUNK_CHARS:
            parts.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        parts.append(current)
    return parts


def to_wav(samples: np.ndarray) -> bytes:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(pcm.tobytes())
    return buffer.getvalue()


@asynccontextmanager
async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
    state["model"] = load_model()
    state["speakers"] = [s for s in state["model"].speakers if s != "random"]
    yield
    state.clear()


app = FastAPI(title="evarisk tts", lifespan=lifespan)


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@app.get("/", response_class=HTMLResponse)
def page() -> str:
    """Страница проверки на слух: поле текста, выбор диктора, плеер."""
    return PAGE.read_text(encoding="utf-8")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "engine": ENGINE,
        "speakers": state["speakers"],
        "default_speaker": DEFAULT_SPEAKER,
        "rates": list(RATES),
        "default_rate": DEFAULT_RATE,
        "sample_rate": SAMPLE_RATE,
    }


@app.post("/synthesize")
def synthesize(req: SynthesisRequest) -> Response:
    if req.language != "ru":
        return error(400, "language", f"язык {req.language!r} не поддерживается, модель русская")
    speaker = req.speaker or DEFAULT_SPEAKER
    if speaker not in state["speakers"]:
        return error(400, "speaker", f"диктор {speaker!r} неизвестен, доступны: {', '.join(state['speakers'])}")
    rate = req.rate or DEFAULT_RATE
    if rate not in RATES:
        return error(400, "rate", f"темп {rate!r} неизвестен, доступны: {', '.join(RATES)}")
    found = sorted(set(UNSPEAKABLE.findall(req.text)))
    if found:
        return error(
            422,
            "unspeakable",
            "текст должен быть записан словами: модель не произносит цифры и латиницу и пропускает их молча; "
            f"найдено: {' '.join(found[:20])}",
        )
    pause = np.zeros(int(SAMPLE_RATE * PAUSE_SECONDS), dtype=np.float32)
    pieces: list[np.ndarray] = []
    for part in chunks(req.text):
        try:
            # Темп задаётся разметкой SSML; текст экранируется, потому что разметка это XML.
            ssml = f'<speak><prosody rate="{rate}">{escape(part)}</prosody></speak>'
            audio = state["model"].apply_tts(
                ssml_text=ssml, speaker=speaker, sample_rate=SAMPLE_RATE, put_accent=True, put_yo=True
            )
        except ValueError as exc:
            return error(422, "synthesis", f"модель не смогла произнести фрагмент {part[:60]!r}: {exc}")
        pieces += [audio.numpy().astype(np.float32), pause]
    return Response(content=to_wav(np.concatenate(pieces[:-1])), media_type="audio/wav")
