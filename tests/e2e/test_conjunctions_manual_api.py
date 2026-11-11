from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from evarisk.api.service import latest_conjunctions, manual_path
from evarisk.config import Settings
from evarisk.store.raw import RawStore

pytestmark = pytest.mark.e2e

HTML = Path("tests/fixtures/socrates/table_iss_2026-09-17.html").read_bytes()
# Строка «Data current as of 2026 Sep 17 22:14:23 UTC» фикстуры.
COMPUTED = datetime(2026, 9, 17, 22, 14, 23, tzinfo=UTC)


def seed_socrates(store: RawStore) -> None:
    store.put(
        source="socrates",
        product="conjunctions_iss",
        url="https://celestrak.org/SOCRATES/table-socrates.php?NAME=ISS%20(ZARYA)&ORDER=MINRANGE&MAX=100",
        content=HTML,
        fetched_at=COMPUTED,
        published_at=COMPUTED,
        published_at_kind="exact",
        content_type="text/html",
        data_version=f"run {COMPUTED.isoformat()}",
    )


def test_latest_conjunctions_lists_rows_with_levels_and_age(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    seed_socrates(store)
    fresh = latest_conjunctions(store, datetime(2026, 9, 18, 10, 14, 23, tzinfo=UTC))
    assert fresh.available and not fresh.stale and fresh.age_hours == pytest.approx(12.0)
    # Три сближения фикстуры по TCA: 21 сентября 21:20:21 (MAXPROB 3.045e-05, elevated), 23 сентября 07:34:27 и
    # 24 сентября 04:59:21 (MAXPROB ниже 1e-5, none); пристыкованных кораблей в фикстуре нет.
    assert [r.level for r in fresh.conjunctions] == ["elevated", "none", "none"]
    assert fresh.conjunctions[0].secondary_name.startswith("SMDC ONE") and fresh.coorbiting_rows == 0
    assert fresh.provenance is not None and fresh.provenance.source == "socrates"
    late = latest_conjunctions(store, datetime(2026, 9, 19, 0, 0, tzinfo=UTC))
    assert late.stale and "устарел" in late.limitations[0]
    empty = latest_conjunctions(RawStore(tmp_path / "empty"), datetime(2026, 9, 19, tzinfo=UTC))
    assert not empty.available and empty.conjunctions == [] and "нет" in empty.limitations[0]


def test_conjunctions_route_answers_without_an_assessment(settings: Settings, client: TestClient) -> None:
    seed_socrates(RawStore(settings.raw_root))
    body = client.get("/conjunctions/latest").json()
    assert body["available"] is True and len(body["conjunctions"]) == 3
    assert body["data_as_of"] == "2026-09-17T22:14:23Z"


def test_manual_serves_docs_and_refuses_paths_outside(client: TestClient) -> None:
    index = client.get("/manual").json()
    names = {e["name"] for e in index}
    assert {"README.md", "MODEL.md", "DECISIONS.md", "API.md"} <= names
    assert "MODULE_README_TEMPLATE.md" not in names
    model = client.get("/manual/MODEL.md")
    assert model.status_code == 200 and model.headers["content-type"].startswith("text/markdown")
    assert model.text.startswith("# Модель")
    for bad in ("../pyproject.toml", "../.env", "../src/evarisk/config.py", "task/nothing.md"):
        assert client.get(f"/manual/{bad}").status_code == 404, bad
    assert manual_path(Path("."), "../.env.example") is None
