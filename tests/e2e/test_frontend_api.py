from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from evarisk.api.app import create_app
from evarisk.config import Settings
from evarisk.store.raw import RawStore

pytestmark = pytest.mark.e2e

# Эфемерида фикстуры conftest: CREATION_DATE 2024-05-10T18:51:53.202, покрытие 2024-05-10T12:00Z–2024-05-25T12:00Z.
REVIEW_6H = {"start": "2024-05-11T00:00:00Z", "end": "2024-05-11T06:00:00Z", "mode": "review"}
GP_FIXTURE = Path("tests/fixtures/celestrak/gp_25544.json")


def test_trajectory_review_six_hours_gives_every_minute(client: TestClient) -> None:
    r = client.get("/trajectory", params=REVIEW_6H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "oem" and body["is_reconstruction"] is True
    assert body["epoch"] == "2024-05-10T18:51:53.202000Z" and body["age_at_query_hours"] is None
    assert body["provenance"]["source"] == "nasa_oem" and body["provenance"]["product"] == "oem"
    samples = body["samples"]
    assert len(samples) == 361
    assert samples[0]["t"] == "2024-05-11T00:00:00Z" and samples[-1]["t"] == "2024-05-11T06:00:00Z"
    assert samples[1]["t"] == "2024-05-11T00:01:00Z"
    assert all(s["level"] == "unknown" for s in samples)
    # Ожидания из orbit/README.md на 2024-05-11: высота 400–435 км, |широта| не выше наклонения 51.8°.
    assert all(400.0 <= s["alt_km"] <= 435.0 for s in samples)
    assert all(abs(s["lat_deg"]) <= 51.8 and -180.0 < s["lon_deg"] <= 180.0 for s in samples)
    assert any(s["sunlit"] for s in samples)
    assert set(samples[0]) == {"t", "lat_deg", "lon_deg", "alt_km", "sunlit", "mlat_deg", "in_saa", "level"}
    assert any("level у всех точек unknown" in lim for lim in body["limitations"])
    assert any("реконструкция" in lim for lim in body["limitations"])


def test_trajectory_step_and_current_mode(client: TestClient) -> None:
    r = client.get("/trajectory", params={**REVIEW_6H, "step_minutes": 5})
    assert r.status_code == 200 and len(r.json()["samples"]) == 73
    assert r.json()["samples"][1]["t"] == "2024-05-11T00:05:00Z"
    r = client.get("/trajectory", params={**REVIEW_6H, "mode": "current"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_reconstruction"] is False and body["age_at_query_hours"] > 0
    assert not any("реконструкция" in lim for lim in body["limitations"])


def test_trajectory_rejects_bad_span_step_mode_and_naive_dates(client: TestClient) -> None:
    r = client.get(
        "/trajectory", params={"start": "2024-05-11T00:00:00Z", "end": "2024-05-13T01:00:00Z", "mode": "review"}
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation"
    assert "48 часов" in r.json()["error"]["message"]
    r = client.get("/trajectory", params={"start": "2024-05-11T00:00:00Z", "end": "2024-05-11T00:00:00Z"})
    assert r.status_code == 400
    r = client.get("/trajectory", params={"start": "2024-05-11T00:00:00", "end": "2024-05-11T06:00:00"})
    assert r.status_code == 400 and "наивная" in r.json()["error"]["message"]
    r = client.get("/trajectory", params={**REVIEW_6H, "step_minutes": 11})
    assert r.status_code == 400
    r = client.get("/trajectory", params={**REVIEW_6H, "mode": "replay"})
    assert r.status_code == 400
    r = client.get("/trajectory", params={"start": "2024-05-11T00:00:00Z"})
    assert r.status_code == 400


def test_trajectory_outside_ephemeris_is_409(client: TestClient) -> None:
    r = client.get(
        "/trajectory", params={"start": "2024-06-01T00:00:00Z", "end": "2024-06-01T06:00:00Z", "mode": "review"}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_trajectory"
    # Час запаса: эфемерида начинается 12:00Z, интервал с 12:30Z не покрыт с запасом.
    r = client.get(
        "/trajectory", params={"start": "2024-05-10T12:30:00Z", "end": "2024-05-10T14:00:00Z", "mode": "review"}
    )
    assert r.status_code == 409


def test_orbit_latest_without_gp_record(client: TestClient) -> None:
    r = client.get("/orbit/latest")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["omm"] is None and body["provenance"] is None
    oem = body["oem"]
    assert oem["creation_date"] == "2024-05-10T18:51:53.202000Z"
    assert oem["start_time"] == "2024-05-10T12:00:00Z" and oem["stop_time"] == "2024-05-25T12:00:00Z"
    assert oem["provenance"]["source"] == "nasa_oem" and oem["provenance"]["data_version"] == "oem 2024-05-10"


def test_orbit_latest_with_gp_record(client: TestClient, settings: Settings) -> None:
    RawStore(settings.raw_root).put(
        source="celestrak",
        product="gp",
        url="https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=JSON",
        content=GP_FIXTURE.read_bytes(),
        fetched_at=datetime(2026, 9, 18, 4, 0, tzinfo=UTC),
        published_at=datetime(2026, 9, 18, 4, 0, tzinfo=UTC),
        published_at_kind="derived",
        content_type="application/json",
        data_version="gp latest",
    )
    r = client.get("/orbit/latest")
    assert r.status_code == 200, r.text
    body = r.json()
    omm = body["omm"]
    assert omm["NORAD_CAT_ID"] == 25544 and omm["OBJECT_NAME"] == "ISS (ZARYA)"
    assert omm["EPOCH"] == "2026-09-18T03:25:38.782272" and omm["MEAN_MOTION"] == 15.49160218
    prov = body["provenance"]
    assert prov["source"] == "celestrak" and prov["product"] == "gp" and prov["data_version"] == "gp latest"
    assert prov["published_at"] == "2026-09-18T04:00:00Z" and prov["published_at_kind"] == "derived"
    assert body["oem"] is not None


def test_rules_snapshot_from_configs(client: TestClient) -> None:
    r = client.get("/rules")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["windows"]["step_minutes"] == 15 and body["windows"]["critical_excludes"] is True
    assert body["sep"]["s1_pfu"] == 10 and body["sep"]["suit_threshold_mev"] == 30
    assert "sources" not in body["sep"]
    sources = body["sources"]
    assert set(sources) == {"windows", "sep", "meteors", "conjunctions", "dose_proxy"}
    assert sources["windows"] is None
    assert "s_scale" in sources["sep"] and sources["sep"]["s_scale"].startswith("https://")
    assert sources["meteors"] and sources["conjunctions"] and sources["dose_proxy"]


def test_frontend_build_is_served_at_root_and_old_page_at_dev(
    settings: Settings, engine: Engine, tmp_path: Path
) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>MISIS GO</title><div id=app></div>", encoding="utf-8")
    (dist / "assets" / "index.js").write_text("export {};", encoding="utf-8")
    c = TestClient(create_app(settings.model_copy(update={"frontend_dist": dist}), engine=engine))
    assert "MISIS GO" in c.get("/").text
    assert c.get("/assets/index.js").status_code == 200
    assert "evarisk" in c.get("/dev/").text and c.get("/dev/app.js").status_code == 200
    assert c.get("/health").status_code == 200 and c.get("/rules").status_code == 200
    assert c.get("/orbit/latest").status_code == 200


def test_without_frontend_build_old_page_stays_at_root(settings: Settings, engine: Engine, tmp_path: Path) -> None:
    c = TestClient(create_app(settings.model_copy(update={"frontend_dist": tmp_path / "missing"}), engine=engine))
    assert "evarisk" in c.get("/").text and c.get("/app.js").status_code == 200
    assert c.get("/dev/").status_code == 404
    assert c.get("/health").status_code == 200
