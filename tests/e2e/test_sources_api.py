from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from evarisk.config import Settings
from evarisk.db.jobs import JobsRepo
from evarisk.db.source_state import SourceStateRepo
from evarisk.sources.http import HttpFetcher
from evarisk.store.raw import RawRecord, RawStore
from evarisk.worker.jobs import run_pending
from evarisk.worker.loop import run_once

pytestmark = pytest.mark.e2e

T = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
REGISTRY = {"nasa_oem", "celestrak", "swpc_live", "donki", "gfz_kp", "socrates", "meteor_docs"}


class FakeOem:
    name = "nasa_oem"
    products: tuple[str, ...] = ("oem",)
    cadence = timedelta(hours=6)
    supports_archive = True

    def __init__(self) -> None:
        self.calls = 0

    def fetch_current(self, fetcher: HttpFetcher, store: RawStore) -> list[RawRecord]:
        self.calls += 1
        rec, _ = store.put(
            source=self.name,
            product="oem",
            url="https://x.invalid/oem-new.txt",
            content=f"call {self.calls}".encode(),
            fetched_at=T,
            published_at=T,
            published_at_kind="exact",
            content_type="text/plain",
            data_version="oem 2026-09-18",
        )
        return [rec]

    def fetch_archive(self, fetcher: HttpFetcher, store: RawStore, start: date, end: date) -> list[RawRecord]:
        return []


def clock() -> datetime:
    return T


def test_sources_list_registry_state_and_store(client: TestClient) -> None:
    r = client.get("/sources")
    assert r.status_code == 200
    by_name = {s["name"]: s for s in r.json()}
    # Реестр адаптеров плюс архивные источники хранилища без адаптера: их тоже можно отключить для проверки.
    assert set(by_name) >= REGISTRY
    archive_only = {name for name, s in by_name.items() if s["archive_only"]}
    assert archive_only == set(by_name) - REGISTRY and "goes_sgps" in archive_only
    assert by_name["goes_sgps"]["cadence_s"] is None and by_name["goes_sgps"]["records"] > 0
    oem = by_name["nasa_oem"]
    assert oem["products"] == ["oem"] and oem["cadence_s"] == 6 * 3600 and oem["supports_archive"] is True
    assert oem["state"] == "active" and oem["reason"] is None and oem["last_success_at"] is None
    assert oem["records"] == 1 and oem["last_record_fetched_at"] == "2026-09-18T00:00:00Z"
    gp = by_name["celestrak"]
    assert gp["supports_archive"] is False and gp["records"] == 0 and gp["last_record_fetched_at"] is None


def test_freeze_source_and_worker_skips_it(client: TestClient, settings: Settings, engine: Engine) -> None:
    r = client.post("/sources/nasa_oem/state", json={"state": "frozen", "reason": "проверка поведения"})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "frozen" and r.json()["reason"] == "проверка поведения"
    assert r.json()["changed_at"] is not None
    listed = {s["name"]: s for s in client.get("/sources").json()}
    assert listed["nasa_oem"]["state"] == "frozen"
    assert client.get("/health").json()["worker_last_tick"] is None
    oem = FakeOem()
    states = SourceStateRepo(engine)
    outcome = run_once(T, RawStore(settings.raw_root), HttpFetcher(httpx.Client()), {"nasa_oem": oem}, states)
    assert outcome == {"nasa_oem": "skipped: frozen"} and oem.calls == 0
    assert client.get("/health").json()["worker_last_tick"] == "2026-09-18T12:00:00Z"
    r = client.post("/sources/nasa_oem/state", json={"state": "active", "reason": "проверка завершена"})
    assert r.status_code == 200 and r.json()["state"] == "active"
    later = T + timedelta(minutes=1)
    outcome = run_once(later, RawStore(settings.raw_root), HttpFetcher(httpx.Client()), {"nasa_oem": oem}, states)
    assert outcome == {"nasa_oem": "fetched 1"} and oem.calls == 1
    listed = {s["name"]: s for s in client.get("/sources").json()}
    assert listed["nasa_oem"]["last_success_at"] == "2026-09-18T12:01:00Z" and listed["nasa_oem"]["records"] == 2


def test_refresh_creates_job_that_fetches_source(client: TestClient, settings: Settings, engine: Engine) -> None:
    r = client.post("/sources/nasa_oem/refresh")
    assert r.status_code == 202 and r.json()["status"] == "queued"
    job_id = r.json()["job_id"]
    queued = client.get(f"/jobs/{job_id}").json()
    assert queued["kind"] == "refresh" and queued["params"] == {"source": "nasa_oem"}
    oem = FakeOem()
    handled = run_pending(
        JobsRepo(engine),
        RawStore(settings.raw_root),
        settings,
        T,
        registry={"nasa_oem": oem},
        fetcher=HttpFetcher(httpx.Client()),
        clock=clock,
    )
    assert handled == 1 and oem.calls == 1
    done = client.get(f"/jobs/{job_id}").json()
    assert done["status"] == "done" and done["progress"]["step"] == "fetched 1" and done["assessment_id"] is None
    listed = {s["name"]: s for s in client.get("/sources").json()}
    assert listed["nasa_oem"]["records"] == 2 and listed["nasa_oem"]["last_success_at"] == "2026-09-18T12:00:00Z"
    assert listed["nasa_oem"]["last_record_fetched_at"] == "2026-09-18T12:00:00Z"


def test_refresh_of_frozen_source_fails_with_reason(client: TestClient, settings: Settings, engine: Engine) -> None:
    client.post("/sources/nasa_oem/state", json={"state": "frozen", "reason": "проверка поведения"})
    job_id = client.post("/sources/nasa_oem/refresh").json()["job_id"]
    oem = FakeOem()
    run_pending(JobsRepo(engine), RawStore(settings.raw_root), settings, T, registry={"nasa_oem": oem}, clock=clock)
    failed = client.get(f"/jobs/{job_id}").json()
    assert failed["status"] == "failed" and "frozen" in failed["error"] and oem.calls == 0


def test_unknown_source_and_bad_state_are_rejected(client: TestClient) -> None:
    r = client.post("/sources/nope/state", json={"state": "frozen", "reason": "x"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = client.post("/sources/nope/refresh")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = client.post("/sources/nasa_oem/state", json={"state": "paused", "reason": "x"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation"
    r = client.post("/sources/nasa_oem/state", json={"state": "frozen"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation"
    assert {s["name"]: s for s in client.get("/sources").json()}["nasa_oem"]["state"] == "active"


REVIEW_MAY_10 = {
    "mode": "review",
    "eva_start": "2024-05-10T14:00:00Z",
    "duration_hours": 6,
    "search_start": "2024-05-10T13:00:00Z",
    "search_end": "2024-05-10T22:00:00Z",
}


def test_disabled_source_is_excluded_from_the_calculation_and_never_reads_as_favourable(client: TestClient) -> None:
    before = client.post("/assessments", json=REVIEW_MAY_10).json()
    assert before["data_availability"]["sep_protons"] == "available" and not before["recommendation"]["insufficient"]
    r = client.post("/sources/goes_sgps/state", json={"state": "disabled", "reason": "проверка деградации"})
    assert r.status_code == 200 and r.json()["state"] == "disabled" and r.json()["archive_only"] is True
    degraded = client.post("/assessments", json=REVIEW_MAY_10).json()
    assert degraded["data_availability"]["sep_protons"] == "missing"
    rec = degraded["recommendation"]
    assert rec["insufficient"] is True and rec["preferred"] is None
    assert "нет данных по линии sep_protons (missing)" in rec["reason"]
    assert any("отключены пользователем" in lim and "goes_sgps" in lim for lim in degraded["limitations"])
    assert all(iv["level"] == "unknown" for iv in degraded["lines"][0]["intervals"])
    # Доказательства прежнего расчёта остаются доступными и при отключённом источнике.
    raw_id = next(
        p["raw_id"] for iv in before["lines"][0]["intervals"] for p in iv["provenance"] if p["source"] == "goes_sgps"
    )
    assert client.get(f"/evidence/{raw_id}").status_code == 200
    assert client.post("/sources/goes_sgps/refresh").status_code == 409
    client.post("/sources/goes_sgps/state", json={"state": "active", "reason": "проверка окончена"})
    restored = client.post("/assessments", json=REVIEW_MAY_10).json()
    assert restored["data_availability"]["sep_protons"] == "available"


def test_all_sources_disabled_gives_no_recommendation(client: TestClient) -> None:
    names = [s["name"] for s in client.get("/sources").json()]
    for name in names:
        client.post(f"/sources/{name}/state", json={"state": "disabled", "reason": "проверка деградации"})
    r = client.post("/assessments", json=REVIEW_MAY_10)
    # Без эфемериды расчёт невозможен: 409 с текстом, а не рекомендация.
    assert r.status_code == 409 and "эфемерид" in r.json()["error"]["message"].lower()
