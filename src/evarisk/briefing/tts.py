from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

SYNTHESIZE_PATH = "/synthesize"
WAV_TYPE = "audio/wav"
TIMEOUT_S = 30.0
NOT_CONFIGURED = "синтез речи не настроен: переменная EVARISK_TTS_URL не задана, озвучка средствами браузера"
BAD_STATUS = "сервис синтеза речи ответил {status}"
BAD_TYPE = "сервис синтеза речи вернул {content_type} вместо audio/wav"
EMPTY_BODY = "сервис синтеза речи вернул пустое тело"
UNREACHABLE = "сервис синтеза речи недоступен: {error}"


@dataclass(frozen=True)
class SynthesisResult:
    """Результат синтеза: файл или причина, по которой его нет; отказ синтеза не отменяет текст сводки."""

    path: Path | None
    error: str | None


class SpeechSynthesizer(Protocol):
    name: str

    def synthesize(self, text: str, destination: Path) -> SynthesisResult: ...


class NoSynthesizer:
    """Состояние по умолчанию: озвучки на сервере нет, `audio_url` пуст, причина названа."""

    name = "none"

    def synthesize(self, text: str, destination: Path) -> SynthesisResult:
        return SynthesisResult(path=None, error=NOT_CONFIGURED)


class HttpSynthesizer:
    """Локальная служба синтеза: `POST {base_url}/synthesize` с `{"text", "language"}` отдаёт WAV.

    Любой отказ службы это `SynthesisResult` с причиной, не исключение: сводка выдаётся и без звука.
    """

    name = "http"

    def __init__(self, base_url: str, timeout_s: float = TIMEOUT_S, client: httpx.Client | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._client = client

    def _post(self, text: str, language: str) -> httpx.Response:
        payload = {"text": text, "language": language}
        if self._client is not None:
            return self._client.post(f"{self.base_url}{SYNTHESIZE_PATH}", json=payload, timeout=self.timeout_s)
        with httpx.Client(timeout=self.timeout_s) as client:
            return client.post(f"{self.base_url}{SYNTHESIZE_PATH}", json=payload)

    def synthesize(self, text: str, destination: Path, language: str = "ru") -> SynthesisResult:
        try:
            response = self._post(text, language)
        except httpx.HTTPError as exc:
            return SynthesisResult(path=None, error=UNREACHABLE.format(error=f"{type(exc).__name__}: {exc}"))
        if response.status_code != 200:
            return SynthesisResult(path=None, error=BAD_STATUS.format(status=response.status_code))
        content_type = response.headers.get("content-type", "")
        if not content_type.startswith(WAV_TYPE):
            return SynthesisResult(path=None, error=BAD_TYPE.format(content_type=content_type or "пустой тип"))
        if not response.content:
            return SynthesisResult(path=None, error=EMPTY_BODY)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        return SynthesisResult(path=destination, error=None)


def make_synthesizer(tts_url: str | None) -> SpeechSynthesizer:
    """Служба синтеза по `EVARISK_TTS_URL`; без переменной озвучка остаётся на стороне браузера."""
    return NoSynthesizer() if not tts_url else HttpSynthesizer(tts_url)
