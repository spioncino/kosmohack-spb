import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from evarisk.api.app import MAX_BODY_BYTES, create_app
from evarisk.config import Settings

pytestmark = pytest.mark.e2e

# Заголовки ставит сам Cloudflare на стороне туннеля; запрос с машины сервиса их не несёт.
TUNNEL = {"cf-connecting-ip": "203.0.113.7", "cf-ray": "8d2f1a0c0e5b1234-FRA"}
STATE = {"state": "frozen", "reason": "проверка поведения"}


@pytest.fixture
def open_client(settings: Settings, engine: Engine) -> TestClient:
    return TestClient(create_app(settings.model_copy(update={"tunnel_readonly": False}), engine=engine))


def test_body_above_the_limit_is_rejected_before_it_is_read(client: TestClient) -> None:
    big = b"x" * (MAX_BODY_BYTES + 1)
    r = client.post("/assessments", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "too_large" and str(MAX_BODY_BYTES) in r.json()["error"]["message"]


def test_a_body_within_the_limit_is_handled_as_before(client: TestClient) -> None:
    r = client.post("/assessments", content=b"x" * 16, headers={"content-type": "application/json"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation"


def test_through_the_tunnel_the_state_of_sources_does_not_change(client: TestClient) -> None:
    r = client.post("/sources/nasa_oem/state", json=STATE, headers=TUNNEL)
    assert r.status_code == 403 and r.json()["error"]["code"] == "tunnel_readonly"
    assert "туннель" in r.json()["error"]["message"] and "EVARISK_TUNNEL_READONLY" in r.json()["error"]["message"]
    refresh = client.post("/sources/nasa_oem/refresh", headers=TUNNEL)
    assert refresh.status_code == 403 and refresh.json()["error"]["code"] == "tunnel_readonly"
    listed = {s["name"]: s for s in client.get("/sources", headers=TUNNEL).json()}
    assert listed["nasa_oem"]["state"] == "active"


def test_the_rest_of_the_service_works_through_the_tunnel(client: TestClient) -> None:
    assert client.get("/health", headers=TUNNEL).status_code == 200
    body = client.post(
        "/assessments",
        headers=TUNNEL,
        json={
            "mode": "review",
            "eva_start": "2024-05-10T14:00:00Z",
            "duration_hours": 6,
            "search_start": "2024-05-10T13:00:00Z",
            "search_end": "2024-05-10T22:00:00Z",
        },
    )
    assert body.status_code == 200
    assert client.get(f"/assessments/{body.json()['id']}/export", headers=TUNNEL).status_code == 200


def test_without_cloudflare_headers_the_state_changes(client: TestClient) -> None:
    r = client.post("/sources/nasa_oem/state", json=STATE)
    assert r.status_code == 200 and r.json()["state"] == "frozen"
    assert client.post("/sources/nasa_oem/refresh").status_code == 202


def test_the_guard_is_switched_off_by_the_setting(open_client: TestClient) -> None:
    r = open_client.post("/sources/nasa_oem/state", json=STATE, headers=TUNNEL)
    assert r.status_code == 200 and r.json()["state"] == "frozen"
