from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from evarisk.api.app import create_app
from evarisk.config import Settings
from evarisk.db.engine import init_schema, make_engine
from evarisk.store.raw import RawStore

F = datetime(2026, 9, 18, tzinfo=UTC)


def seed(raw: Path) -> RawStore:
    store = RawStore(raw)
    store.put(
        source="goes_sgps",
        product="sgps_l2_avg5m",
        url="https://x.invalid/g.nc",
        content=Path("tests/fixtures/goes/sci_sgps-l2-avg5m_g18_d20240510_v3-0-2.nc").read_bytes(),
        fetched_at=F,
        published_at=datetime(2024, 5, 11, 0, 5, tzinfo=UTC),
        published_at_kind="derived",
        content_type="application/x-netcdf",
        data_version="reprocessed v3-0-2",
    )
    store.put(
        source="gfz_kp",
        product="kp",
        url="https://x.invalid/kp.json",
        content=Path("tests/fixtures/gfz/kp_2024-05-10_11.json").read_bytes(),
        fetched_at=F,
        published_at=datetime(2024, 7, 15, tzinfo=UTC),
        published_at_kind="derived",
        content_type="application/json",
        data_version="definitive",
    )
    store.put(
        source="nasa_oem",
        product="oem",
        url="https://x.invalid/oem.txt",
        content=Path("tests/fixtures/nasa_oem/2024-05-12_ISS.OEM_J2K_EPH.txt").read_bytes(),
        fetched_at=F,
        published_at=datetime(2024, 5, 10, 18, 51, 53, tzinfo=UTC),
        published_at_kind="exact",
        content_type="text/plain",
        data_version="oem 2024-05-10",
    )
    # Почасовой прогноз NASA MEO 2024 (NTRS 20230015158, распространение 2 ноября 2023): фрагмент настоящего
    # flux_data.txt за 9–13 мая, 6–10 июня и 13–15 декабря 2024 года.
    store.put(
        source="meteor_docs",
        product="meo_flux_2024",
        url="https://ntrs.nasa.gov/api/citations/20230015158/downloads/flux_data.txt",
        content=Path("tests/fixtures/meo/flux_data_2024_excerpt.txt").read_bytes(),
        fetched_at=F,
        published_at=datetime(2023, 11, 2, 23, 59, tzinfo=UTC),
        published_at_kind="derived",
        content_type="text/plain",
        data_version="MEO 2024, NTRS 20230015158, distributed 2023-11-02",
    )
    return store


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    raw = tmp_path / "raw"
    seed(raw)
    # Файл .env машины разработки в тестах не читается: иначе локальный EVARISK_TTS_URL включал бы обращение в сеть.
    # Докачка архива по запросу выключена по той же причине; её проверяет test_worker_backfill.py на подмене.
    return Settings(
        _env_file=None,
        raw_root=raw,
        assessments_root=tmp_path / "assessments",
        database_url="sqlite:///:memory:",
        mirror_root=tmp_path / "mirror",
        archive_backfill=False,
    )


@pytest.fixture
def engine() -> Engine:
    eng = make_engine("sqlite:///:memory:")
    init_schema(eng)
    return eng


@pytest.fixture
def client(settings: Settings, engine: Engine) -> TestClient:
    return TestClient(create_app(settings, engine=engine))
