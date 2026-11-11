import json
from pathlib import Path

import httpx

from evarisk.briefing.tts import HttpSynthesizer, NoSynthesizer, make_synthesizer

WAV = b"RIFF$\x00\x00\x00WAVEdata"
SPEECH = "четыре ноль-ноль"


def synthesizer(handler: object) -> HttpSynthesizer:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return HttpSynthesizer("http://tts.invalid/", client=httpx.Client(transport=transport))


def test_wav_of_the_service_is_written_next_to_the_assessment(tmp_path: Path) -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"path": request.url.path, **json.loads(request.content)})
        return httpx.Response(200, content=WAV, headers={"content-type": "audio/wav"})

    result = synthesizer(handler).synthesize(SPEECH, tmp_path / "a.briefing.summary.ru.wav")
    assert seen == [{"path": "/synthesize", "text": SPEECH, "language": "ru"}]
    assert result.error is None and result.path is not None
    assert result.path.read_bytes() == WAV


def test_refusal_of_the_service_leaves_the_briefing_without_audio(tmp_path: Path) -> None:
    def rejects(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "в тексте цифры"})

    result = synthesizer(rejects).synthesize("04:00", tmp_path / "a.wav")
    assert result.path is None and result.error is not None and "422" in result.error
    assert not (tmp_path / "a.wav").exists()


def test_wrong_type_and_unreachable_service_are_named(tmp_path: Path) -> None:
    def html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html/>", headers={"content-type": "text/html"})

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("соединение не установлено")

    wrong = synthesizer(html).synthesize(SPEECH, tmp_path / "a.wav")
    assert wrong.path is None and wrong.error is not None and "text/html" in wrong.error
    gone = synthesizer(broken).synthesize(SPEECH, tmp_path / "a.wav")
    assert gone.path is None and gone.error is not None and "ConnectError" in gone.error


def test_without_the_variable_the_synthesizer_is_the_empty_one(tmp_path: Path) -> None:
    assert isinstance(make_synthesizer(None), NoSynthesizer)
    assert isinstance(make_synthesizer(""), NoSynthesizer)
    assert isinstance(make_synthesizer("http://tts.invalid"), HttpSynthesizer)
    result = NoSynthesizer().synthesize(SPEECH, tmp_path / "a.wav")
    assert result.path is None and result.error is not None and "EVARISK_TTS_URL" in result.error
