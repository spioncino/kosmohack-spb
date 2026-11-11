"""Ретранслятор модели на компьютере команды (D91).

Сервер без ключей модели шлёт запрос сюда через туннель; ретранслятор вызывает локальный `make_client()` с ключами
из `.env.llm` и возвращает текст. Ключи не покидают компьютер, наружу уходит только ответ модели. Доступ по токену
`EVARISK_LLM_RELAY_TOKEN` из окружения или `.env.relay`; без токена ретранслятор отвечает 401 на любой запрос.
Запуск: `uv run uvicorn evarisk.briefing.relay:app --host 127.0.0.1 --port 8020`.
"""

import hmac

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from evarisk.briefing.llm import LlmClient, Message, make_client

BEARER = "Bearer "


class RelaySettings(BaseSettings):
    """Токен доступа к ретранслятору; файл `.env.relay` исключён из git, как все `.env.*`."""

    model_config = SettingsConfigDict(env_file=".env.relay", extra="ignore")

    evarisk_llm_relay_token: SecretStr | None = None


class CompleteRequest(BaseModel):
    messages: list[Message]


class CompleteResponse(BaseModel):
    text: str
    model: str


def create_app(client: LlmClient | None, token: SecretStr | None) -> FastAPI:
    """Приложение ретранслятора с заданным клиентом модели; тесты подставляют поддельный клиент."""
    relay = FastAPI(title="evarisk llm relay", docs_url=None, redoc_url=None, openapi_url=None)

    def authorize(authorization: str | None = Header(default=None)) -> None:
        expected = token.get_secret_value() if token is not None else ""
        given = authorization[len(BEARER) :] if authorization and authorization.startswith(BEARER) else ""
        if not expected or not given or not hmac.compare_digest(given.encode(), expected.encode()):
            raise HTTPException(status_code=401, detail="нужен токен ретранслятора")

    @relay.get("/health")
    def health() -> dict[str, object]:
        return {"status": "ok", "model": client is not None}

    @relay.post("/complete", dependencies=[Depends(authorize)])
    def complete(body: CompleteRequest) -> CompleteResponse:
        if client is None:
            raise HTTPException(status_code=503, detail="модель на компьютере команды не настроена")
        try:
            text = client.complete(body.messages)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"модель не ответила: {type(exc).__name__}") from exc
        return CompleteResponse(text=text, model=client.model)

    return relay


def build_app() -> FastAPI:
    """Приложение с локальной моделью и токеном из окружения; ошибка создания клиента это ответ 503, не падение."""
    try:
        client = make_client()
    except Exception as exc:
        print(f"клиент модели не создан: {type(exc).__name__}")
        client = None
    return create_app(client, RelaySettings().evarisk_llm_relay_token)


def __getattr__(name: str) -> FastAPI:
    # `uvicorn evarisk.briefing.relay:app` строит приложение при запуске; импорт модуля в тестах ключей не читает.
    if name == "app":
        return build_app()
    raise AttributeError(name)
