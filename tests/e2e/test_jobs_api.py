from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from evarisk.api.app import create_app
from evarisk.config import Settings
from evarisk.db.jobs import JobsRepo
from evarisk.store.raw import RawStore
from evarisk.worker.jobs import run_pending

pytestmark = pytest.mark.e2e

T = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
# Эфемерида фикстуры начинается 2024-05-10T12:00Z, период поиска с запасом в час начинается в 13:00Z.
REVIEW = {
    "mode": "review",
    "eva_start": "2024-05-10T14:00:00Z",
    "duration_hours": 6,
    "search_start": "2024-05-10T13:00:00Z",
    "search_end": "2024-05-10T22:00:00Z",
}
NO_DATA = {
    "mode": "review",
    "eva_start": "2020-01-01T14:00:00Z",
    "duration_hours": 6,
    "search_start": "2020-01-01T13:00:00Z",
    "search_end": "2020-01-01T22:00:00Z",
}
VOLATILE = {"id", "created_at"}


def stable(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k not in VOLATILE}


def test_job_runs_to_done_and_result_equals_sync(client: TestClient, settings: Settings, engine: Engine) -> None:
    sync = client.post("/assessments", json=REVIEW).json()
    created = client.post("/jobs", json=REVIEW)
    assert created.status_code == 202, created.text
    assert created.json()["status"] == "queued"
    job_id = created.json()["job_id"]
    queued = client.get(f"/jobs/{job_id}").json()
    assert queued["status"] == "queued" and queued["progress"] is None and queued["assessment_id"] is None
    assert queued["kind"] == "assessment" and queued["params"]["eva_start"] == "2024-05-10T14:00:00Z"
    assert queued["started_at"] is None and queued["error"] is None
    assert run_pending(JobsRepo(engine), RawStore(settings.raw_root), settings, T) == 1
    done = client.get(f"/jobs/{job_id}").json()
    assert done["status"] == "done" and done["error"] is None, done
    assert done["progress"]["step"] == "saved" and done["progress"]["fraction"] == 1.0
    assert done["started_at"] == "2026-09-18T12:00:00Z" and done["finished_at"] is not None
    result = client.get(f"/assessments/{done['assessment_id']}")
    assert result.status_code == 200
    assert stable(result.json()) == stable(sync)
    assert result.json()["id"] != sync["id"]


def test_failed_job_reports_error_text(client: TestClient, settings: Settings, engine: Engine) -> None:
    job_id = client.post("/jobs", json=NO_DATA).json()["job_id"]
    assert run_pending(JobsRepo(engine), RawStore(settings.raw_root), settings, T) == 1
    failed = client.get(f"/jobs/{job_id}").json()
    assert failed["status"] == "failed" and failed["assessment_id"] is None
    assert failed["error"].startswith("NoTrajectoryData:") and "эфемерид" in failed["error"]
    assert failed["finished_at"] is not None


def test_invalid_job_body_is_rejected_before_queueing(client: TestClient, engine: Engine) -> None:
    r = client.post("/jobs", json={**REVIEW, "duration_hours": 9})
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation"
    r = client.post("/jobs", json={**REVIEW, "mode": "replay"})
    assert r.status_code == 400 and "cutoff" in r.json()["error"]["message"]
    assert JobsRepo(engine).claim(T) is None


def test_unknown_job_is_404(client: TestClient) -> None:
    r = client.get("/jobs/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_app_builds_engine_from_settings(settings: Settings) -> None:
    app = create_app(settings)
    client = TestClient(app)
    job_id = client.post("/jobs", json=REVIEW).json()["job_id"]
    assert run_pending(JobsRepo(app.state.engine), RawStore(settings.raw_root), settings, T) == 1
    assert client.get(f"/jobs/{job_id}").json()["status"] == "done"


def test_sync_assessment_still_works(client: TestClient) -> None:
    r = client.post("/assessments", json=REVIEW)
    assert r.status_code == 200 and r.json()["lines"][0]["line"] == "sep_protons"
