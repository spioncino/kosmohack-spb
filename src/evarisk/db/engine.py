from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.pool import StaticPool

from evarisk.db.schema import metadata

SQLITE_FILE_PREFIX = "sqlite:///"
SQLITE_MEMORY_URLS = ("sqlite://", "sqlite:///:memory:")
SQLITE_BUSY_TIMEOUT_S = 30


def _listen_sqlite_pragmas(engine: Engine) -> None:
    """Журнал WAL и ожидание занятой базы на каждом подключении к файлу SQLite.

    В один файл пишут api и воркер: без WAL чтение и запись исключают друг друга, а без busy_timeout занятая
    база это немедленная ошибка «database is locked».
    """

    @event.listens_for(engine, "connect")
    def _pragmas(connection: Any, _record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_S * 1000}")
        cursor.close()


def make_engine(url: str) -> Engine:
    if url in SQLITE_MEMORY_URLS:
        return create_engine(url, poolclass=StaticPool, connect_args={"check_same_thread": False})
    if url.startswith(SQLITE_FILE_PREFIX):
        Path(url[len(SQLITE_FILE_PREFIX) :]).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"timeout": SQLITE_BUSY_TIMEOUT_S})
        _listen_sqlite_pragmas(engine)
        return engine
    return create_engine(url)


def init_schema(engine: Engine) -> None:
    metadata.create_all(engine)
