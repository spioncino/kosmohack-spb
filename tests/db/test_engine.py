from pathlib import Path

from sqlalchemy import text

from evarisk.db.engine import SQLITE_BUSY_TIMEOUT_S, init_schema, make_engine


def test_file_sqlite_is_opened_in_wal_with_a_busy_timeout(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'sub' / 'evarisk.db'}")
    init_schema(engine)
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"
        assert conn.execute(text("PRAGMA busy_timeout")).scalar_one() == SQLITE_BUSY_TIMEOUT_S * 1000
    assert (tmp_path / "sub" / "evarisk.db").exists()


def test_memory_sqlite_keeps_its_journal() -> None:
    engine = make_engine("sqlite:///:memory:")
    init_schema(engine)
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar_one() == "memory"


def test_postgres_url_gets_no_sqlite_settings() -> None:
    engine = make_engine("postgresql+psycopg://evarisk:x@db:5432/evarisk")
    assert engine.dialect.name == "postgresql"
    assert engine.pool.__class__.__name__ != "StaticPool"
