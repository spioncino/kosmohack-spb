from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, MetaData, String, Table, Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TIMESTAMP, TypeDecorator, TypeEngine

from evarisk.timeutil import ensure_utc

POSTGRES = "postgresql"
ISO_WITH_ZONE_LENGTH = 32


class UtcDateTime(TypeDecorator[datetime]):
    impl = String
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == POSTGRES:
            return dialect.type_descriptor(TIMESTAMP(timezone=True))
        return dialect.type_descriptor(String(ISO_WITH_ZONE_LENGTH))

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        utc = ensure_utc(value)
        if dialect.name == POSTGRES:
            return utc
        return utc.isoformat()

    def process_result_value(self, value: Any | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return ensure_utc(value)
        return ensure_utc(datetime.fromisoformat(str(value)))


metadata = MetaData()

source_state = Table(
    "source_state",
    metadata,
    Column("source", String(64), primary_key=True),
    Column("state", String(16), nullable=False),
    Column("changed_at", UtcDateTime, nullable=True),
    Column("reason", Text, nullable=True),
    Column("last_attempt_at", UtcDateTime, nullable=True),
    Column("last_success_at", UtcDateTime, nullable=True),
    Column("last_error", Text, nullable=True),
)

jobs = Table(
    "jobs",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("kind", String(32), nullable=False),
    Column("params", JSON, nullable=False),
    Column("status", String(16), nullable=False),
    Column("progress", JSON, nullable=True),
    Column("error", Text, nullable=True),
    Column("created_at", UtcDateTime, nullable=False),
    Column("started_at", UtcDateTime, nullable=True),
    Column("finished_at", UtcDateTime, nullable=True),
    Column("assessment_id", String(64), nullable=True),
)
