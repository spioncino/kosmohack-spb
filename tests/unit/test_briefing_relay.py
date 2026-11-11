import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from evarisk.briefing.llm import (
    LlmSettings,
    LlmUnavailableError,
    Message,
    RelayClient,
    RetryingClient,
    is_auth_failure,
    is_transient,
    make_client,
)
from evarisk.briefing.relay import create_app

TOKEN = "test-relay-token"
MESSAGES: list[Message] = [{"role": "user", "content": "сводка"}]
RELAY_URL = "https://relay.invalid"
QWEN_VARS = ("QWEN_BASE_URL", "QWEN_KEY_ID", "QWEN_SECRET")


class Fake:
    model = "qwen-test"

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.seen: list[list[Message]] = []

    def complete(self, messages: list[Message]) -> str:
        self.seen.append(messages)
        if self.error is not None:
            raise self.error
        return "Окно 09:30–15:30."


def relay(client: Fake | None) -> TestClient:
    return TestClient(create_app(client, SecretStr(TOKEN)))


def test_relay_rejects_requests_without_valid_token() -> None:
    fake = Fake()
    api = relay(fake)
    assert api.post("/complete", json={"messages": MESSAGES}).status_code == 401
    wrong = api.post("/complete", json={"messages": MESSAGES}, headers={"Authorization": "Bearer wrong-token"})
    assert wrong.status_code == 401
    assert fake.seen == []


def test_relay_without_configured_token_rejects_everything() -> None:
    api = TestClient(create_app(Fake(), None))
    assert api.post("/complete", json={"messages": MESSAGES}, headers={"Authorization": "Bearer "}).status_code == 401


def test_relay_answers_with_model_text() -> None:
    fake = Fake()
    response = relay(fake).post("/complete", json={"messages": MESSAGES}, headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert response.json() == {"text": "Окно 09:30–15:30.", "model": "qwen-test"}
    assert fake.seen == [MESSAGES]


def test_relay_reports_503_when_model_fails_or_is_absent() -> None:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    failed = relay(Fake(LlmUnavailableError("модель не ответила"))).post(
        "/complete", json={"messages": MESSAGES}, headers=headers
    )
    assert failed.status_code == 503
    absent = relay(None).post("/complete", json={"messages": MESSAGES}, headers=headers)
    assert absent.status_code == 503


def mock_relay(status: int, seen: list[httpx.Request]) -> RelayClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={"text": "Окно 09:30–15:30.", "model": "qwen35-vlm"})

    return RelayClient(RELAY_URL + "/", SecretStr(TOKEN), transport=httpx.MockTransport(handler))


def test_relay_client_posts_messages_with_token() -> None:
    seen: list[httpx.Request] = []
    assert mock_relay(200, seen).complete(MESSAGES) == "Окно 09:30–15:30."
    assert str(seen[0].url) == RELAY_URL + "/complete"
    assert seen[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(seen[0].content) == {"messages": MESSAGES}


def test_relay_client_errors_follow_retry_rules() -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(httpx.HTTPStatusError) as denied:
        mock_relay(401, seen).complete(MESSAGES)
    assert is_auth_failure(denied.value) and not is_transient(denied.value)
    with pytest.raises(httpx.HTTPStatusError) as gateway:
        mock_relay(502, seen).complete(MESSAGES)
    assert is_transient(gateway.value)
    with pytest.raises(LlmUnavailableError):
        mock_relay(503, seen).complete(MESSAGES)


def test_retrying_relay_does_not_repeat_wrong_token_or_model_down() -> None:
    for status in (401, 503):
        seen: list[httpx.Request] = []
        with pytest.raises((httpx.HTTPStatusError, LlmUnavailableError)):
            RetryingClient(mock_relay(status, seen), retries=3, sleep=lambda _: None).complete(MESSAGES)
        assert len(seen) == 1, status
    seen = []
    with pytest.raises(LlmUnavailableError, match="за 4 попыток"):
        RetryingClient(mock_relay(502, seen), retries=3, sleep=lambda _: None).complete(MESSAGES)
    assert len(seen) == 4


def test_make_client_uses_relay_without_local_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in QWEN_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EVARISK_LLM_RELAY_URL", RELAY_URL)
    monkeypatch.setenv("EVARISK_LLM_RELAY_TOKEN", TOKEN)
    client = make_client(LlmSettings(_env_file=None))  # type: ignore[call-arg]
    assert isinstance(client, RetryingClient) and isinstance(client.inner, RelayClient)


def test_make_client_without_relay_token_or_url_is_template(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*QWEN_VARS, "EVARISK_LLM_RELAY_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EVARISK_LLM_RELAY_URL", RELAY_URL)
    assert make_client(LlmSettings(_env_file=None)) is None  # type: ignore[call-arg]
    monkeypatch.delenv("EVARISK_LLM_RELAY_URL")
    monkeypatch.setenv("EVARISK_LLM_RELAY_TOKEN", TOKEN)
    assert make_client(LlmSettings(_env_file=None)) is None  # type: ignore[call-arg]


def test_the_model_is_shown_by_its_real_name() -> None:
    # Людям показывается имя модели, а не имя развёртывания в API.
    from evarisk.briefing.llm import model_name

    assert model_name("qwen35-vlm") == "Qwen 3.8"
    assert model_name("fake-model") == "fake-model" and model_name(None) is None
