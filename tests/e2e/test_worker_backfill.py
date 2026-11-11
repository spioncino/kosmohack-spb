from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from evarisk.api.app import create_app
from evarisk.config import Settings
from evarisk.db.jobs import JobsRepo
from evarisk.sources.archive_mirror import GFZ, GOES, S3
from evarisk.store.raw import RawStore
from evarisk.worker.jobs import run_pending

pytestmark = pytest.mark.e2e

T = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
OEM = Path("tests/fixtures/nasa_oem/2024-05-12_ISS.OEM_J2K_EPH.txt")
GOES_FILE = Path("tests/fixtures/goes/sci_sgps-l2-avg5m_g18_d20240510_v3-0-2.nc")
KP_FILE = Path("tests/fixtures/gfz/kp_2024-05-10_11.json")
# Эфемерида фикстуры начинается 2024-05-10T12:00Z, период поиска с запасом в час начинается в 13:00Z.
REVIEW = {
    "mode": "review",
    "eva_start": "2024-05-10T14:00:00Z",
    "duration_hours": 6,
    "search_start": "2024-05-10T13:00:00Z",
    "search_end": "2024-05-10T22:00:00Z",
}


class FakeArchive:
    """Подменённые открытые архивы: эфемерида NASA, суточный файл GOES за 10 мая 2024 и Kp GFZ, на остальное 404.

    Kp нужен разбору по архиву: без него доступ частиц при идущем событии не определён, и линия протонов missing.
    """

    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str) -> tuple[int, bytes, dict[str, str]]:
        self.urls.append(url)
        if url.startswith(f"{S3}/?") and "2024-05-" in url:
            return 200, b"<Prefix>iss-coords/2024-05-10/</Prefix>", {}
        if url == f"{S3}/iss-coords/2024-05-10/ISS_OEM/ISS.OEM_J2K_EPH.txt":
            return 200, OEM.read_bytes(), {"content-type": "text/plain"}
        if url.startswith(GFZ):
            return 200, KP_FILE.read_bytes(), {"content-type": "application/json"}
        if url == f"{GOES}/2024/05/":
            return 200, f'<a href="{GOES_FILE.name}">x</a>'.encode(), {}
        if url == f"{GOES}/2024/05/{GOES_FILE.name}":
            return 200, GOES_FILE.read_bytes(), {"content-type": "application/x-netcdf"}
        return 404, b"", {}


def no_network(url: str) -> tuple[int, bytes, dict[str, str]]:
    raise AssertionError(f"сеть не должна вызываться: {url}")


@pytest.fixture
def empty_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        raw_root=tmp_path / "raw",
        assessments_root=tmp_path / "assessments",
        database_url="sqlite:///:memory:",
        mirror_root=tmp_path / "mirror",
        archive_backfill=True,
    )


def test_job_on_an_empty_store_backfills_the_archive_and_finishes(empty_settings: Settings, engine: Engine) -> None:
    client = TestClient(create_app(empty_settings, engine=engine))
    # Синхронный расчёт в сеть не ходит: на пустом хранилище это отказ «нет эфемериды».
    refused = client.post("/assessments", json=REVIEW)
    assert refused.status_code == 409 and "POST /jobs докачает архив" in refused.json()["error"]["message"]
    job_id = client.post("/jobs", json=REVIEW).json()["job_id"]
    archive = FakeArchive()
    store = RawStore(empty_settings.raw_root)
    assert run_pending(JobsRepo(engine), store, empty_settings, T, archive_fetch=archive) == 1
    done = client.get(f"/jobs/{job_id}").json()
    assert done["status"] == "done" and done["error"] is None, done
    result = client.get(f"/assessments/{done['assessment_id']}").json()
    limitations = " | ".join(result["limitations"])
    # Нужны сутки 7–10 мая; архив отдал только 10 мая: докачка названа, недостающие сутки названы.
    assert "архив на период докачан по запросу: 2024-05-07 … 2024-05-10" in limitations
    assert "нет суточных файлов потока протонов за 2024-05-07 … 2024-05-09" in limitations
    sep = next(line for line in result["lines"] if line["line"] == "sep_protons")
    assert sep["availability"] == "available"
    assert len(store.records("nasa_oem")) == 1 and len(store.records("goes_sgps")) == 1


def test_covered_period_and_disabled_setting_never_touch_the_network(
    client: TestClient, settings: Settings, engine: Engine, empty_settings: Settings
) -> None:
    # Фикстура settings держит докачку выключенной: даже непокрытый период не вызывает сеть.
    client.post("/jobs", json=REVIEW)
    assert run_pending(JobsRepo(engine), RawStore(settings.raw_root), settings, T, archive_fetch=no_network) == 1


def test_failed_backfill_is_named_in_the_job_error(empty_settings: Settings, engine: Engine) -> None:
    client = TestClient(create_app(empty_settings, engine=engine))
    job_id = client.post("/jobs", json=REVIEW).json()["job_id"]

    def nothing(url: str) -> tuple[int, bytes, dict[str, str]]:
        return 404, b"", {}

    assert (
        run_pending(JobsRepo(engine), RawStore(empty_settings.raw_root), empty_settings, T, archive_fetch=nothing) == 1
    )
    failed = client.get(f"/jobs/{job_id}").json()
    assert failed["status"] == "failed"
    assert "нет эфемериды" in failed["error"] and "архив на период докачан по запросу" in failed["error"]
