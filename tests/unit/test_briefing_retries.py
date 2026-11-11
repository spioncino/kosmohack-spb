import httpx
import openai
import pytest

from evarisk.briefing.llm import (
    DEFAULT_RETRIES,
    LlmSettings,
    LlmUnavailableError,
    Message,
    RetryingClient,
    is_transient,
)

REQUEST = httpx.Request("POST", "https://llm.invalid/v1/chat/completions")
MESSAGES: list[Message] = [{"role": "user", "content": "сводка"}]


def status_error(code: int) -> openai.APIStatusError:
    return openai.APIStatusError("ответ сервиса", response=httpx.Response(code, request=REQUEST), body=None)


class Flaky:
    """Подмена модели: первые `failures` вызовов бросают `error`, дальше отвечает текстом."""

    model = "qwen-test"

    def __init__(self, failures: int, error: Exception) -> None:
        self.failures = failures
        self.error = error
        self.calls = 0

    def complete(self, messages: list[Message]) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return "Окно 09:30–15:30. Решение за руководителем работ."


def test_transient_failures_are_retried_with_growing_pauses() -> None:
    pauses: list[float] = []
    inner = Flaky(3, openai.APIConnectionError(request=REQUEST))
    client = RetryingClient(inner, retries=5, sleep=pauses.append)
    assert client.complete(MESSAGES).startswith("Окно 09:30–15:30") and inner.calls == 4
    assert pauses == [0.5, 1.0, 2.0] and client.model == "qwen-test"


def test_after_five_retries_the_reason_names_the_attempts() -> None:
    pauses: list[float] = []
    inner = Flaky(100, openai.APITimeoutError(request=REQUEST))
    client = RetryingClient(inner, retries=5, sleep=pauses.append)
    with pytest.raises(LlmUnavailableError, match="модель не ответила за 6 попыток: APITimeoutError"):
        client.complete(MESSAGES)
    # Пять повторов после первой попытки, паузы 0.5, 1, 2, 4 и 4 с (потолок 4 с).
    assert inner.calls == 6 and pauses == [0.5, 1.0, 2.0, 4.0, 4.0]


def test_permanent_errors_are_not_retried() -> None:
    for code in (400, 401, 403, 404):
        inner = Flaky(100, status_error(code))
        with pytest.raises(openai.APIStatusError):
            RetryingClient(inner, retries=5, sleep=lambda _: None).complete(MESSAGES)
        assert inner.calls == 1, code


def test_transient_classification() -> None:
    for code in (408, 409, 429, 500, 502, 503):
        assert is_transient(status_error(code)), code
    assert is_transient(openai.APIConnectionError(request=REQUEST))
    assert is_transient(httpx.ConnectError("нет соединения")) and is_transient(TimeoutError())
    assert not is_transient(status_error(401)) and not is_transient(ValueError("разбор"))


def test_default_is_five_retries() -> None:
    assert DEFAULT_RETRIES == 5 and LlmSettings(_env_file=None).qwen_retries == 5


class Resettable(Flaky):
    """Подмена клиента с токеном: до `reset()` шлюз отвечает 403, после сброса отвечает текстом."""

    def __init__(self, resets_needed: int) -> None:
        super().__init__(0, status_error(403))
        self.resets = 0
        self.resets_needed = resets_needed

    def reset(self) -> None:
        self.resets += 1

    def complete(self, messages: list[Message]) -> str:
        self.calls += 1
        if self.resets < self.resets_needed:
            raise status_error(403)
        return "Окно 09:30–15:30. Решение за руководителем работ."


def test_rejected_token_resets_the_client_and_retries() -> None:
    pauses: list[float] = []
    inner = Resettable(resets_needed=1)
    text = RetryingClient(inner, retries=5, sleep=pauses.append).complete(MESSAGES)
    assert text.startswith("Окно") and inner.resets == 1 and inner.calls == 2 and pauses == [0.5]


def test_token_resets_are_limited_to_two() -> None:
    inner = Resettable(resets_needed=10)
    with pytest.raises(openai.APIStatusError):
        RetryingClient(inner, retries=5, sleep=lambda _: None).complete(MESSAGES)
    assert inner.resets == 2 and inner.calls == 3


def test_auth_failure_without_reset_is_not_retried() -> None:
    inner = Flaky(100, status_error(403))
    with pytest.raises(openai.APIStatusError):
        RetryingClient(inner, retries=5, sleep=lambda _: None).complete(MESSAGES)
    assert inner.calls == 1
