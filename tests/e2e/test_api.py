import io
import json
import zipfile
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from evarisk.config import Settings

pytestmark = pytest.mark.e2e

# Эфемерида фикстуры начинается 2024-05-10T12:00Z, период поиска с запасом в час начинается в 13:00Z.
REVIEW = {
    "mode": "review",
    "eva_start": "2024-05-10T14:00:00Z",
    "duration_hours": 6,
    "search_start": "2024-05-10T13:00:00Z",
    "search_end": "2024-05-10T22:00:00Z",
}


def test_review_assessment_has_lines_windows_and_evidence(client: TestClient) -> None:
    r = client.post("/assessments", json=REVIEW)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lines"][0]["line"] == "sep_protons" and body["data_availability"]["sep_protons"] == "available"
    assert body["lines"][1]["line"] == "meteor_showers" and body["data_availability"]["meteor_showers"] == "available"
    assert all(iv["kind"] == "external_forecast" for iv in body["lines"][1]["intervals"])
    names = [line["line"] for line in body["lines"]]
    assert names == [
        "sep_protons",
        "meteor_showers",
        "conjunctions",
        "dose_proxy",
        "illumination",
        "proton_access_geometry",
    ]
    assert body["data_availability"]["conjunctions"] == "unusable_for_replay"
    assert body["data_availability"]["dose_proxy"] == "available"
    assert body["data_availability"]["illumination"] == "available"
    assert all("illumination" not in w["per_line"] for w in [*body["windows"], body["requested_window"]])
    assert not any("освещённости" in (w["excluded_reason"] or "") for w in body["windows"])
    assert any("условие работ" in lim for lim in body["limitations"])
    assert "dose_proxy_usv_window" in body["lines"][3]["metrics"]
    assert any("SOCRATES" in lim for lim in body["limitations"]) and any(
        "прокси дозы" in lim for lim in body["limitations"]
    )
    assert not all(w["excluded_reason"] for w in body["windows"]), (
        "исторические режимы не должны исключать окна из-за сближений"
    )
    assert len(body["windows"]) >= 2 and body["requested_window"]["window"]["start"] == "2024-05-10T14:00:00Z"
    assert body["recommendation"]["rules_applied"]
    assert body["trajectory"]["provider"] == "oem" and body["trajectory"]["samples"]
    passes = body["trajectory"]["saa_passes"]
    assert passes and all(p["min_b_nt"] < p["threshold_nt"] == 24000.0 for p in passes)
    assert all("IGRF" in p["rule"] and "NTRS 20000085550" in p["rule"] and "минимум |B|" in p["note"] for p in passes)
    assert all(p["provenance"][0]["raw_id"] == body["trajectory"]["provenance"]["raw_id"] for p in passes)
    raw_ids = {p["raw_id"] for iv in body["lines"][0]["intervals"] for p in iv["provenance"]}
    assert raw_ids
    for raw_id in raw_ids:
        ev = client.get(f"/evidence/{raw_id}")
        assert ev.status_code == 200 and ev.json()["record"]["id"] == raw_id
    again = client.get(f"/assessments/{body['id']}")
    assert again.status_code == 200 and again.json() == body


def test_current_mode_with_a_past_window_says_so_and_groups_exclusions(client: TestClient) -> None:
    body = client.post("/assessments", json={**REVIEW, "mode": "current"}).json()
    assert "режим «сейчас» для окна в прошлом" in " ".join(body["limitations"])
    rec = body["recommendation"]
    if rec["insufficient"]:
        # Причины сгруппированы по линии и виду, а не перечислены по каждому окну.
        assert rec["reason"].startswith(f"оснований для выбора нет: все {len(body['windows'])} окон исключены: ")
        assert rec["reason"].count("недостаточно данных") == 0 and rec["reason"].count("нет данных по линии") <= 1


def test_replay_before_data_is_honest(client: TestClient) -> None:
    r = client.post("/assessments", json={**REVIEW, "mode": "replay", "cutoff": "2024-05-10T13:00:00Z"})
    assert r.status_code == 409 or (r.status_code == 200 and r.json()["data_availability"]["sep_protons"] == "missing")
    if r.status_code == 200:
        assert r.json()["recommendation"]["insufficient"] is True


def test_validation_errors_are_explicit(client: TestClient) -> None:
    r = client.post("/assessments", json={**REVIEW, "duration_hours": 9})
    assert r.status_code == 400 and "error" in r.json()
    r = client.post("/assessments", json={**REVIEW, "mode": "replay"})
    assert r.status_code == 400 and "cutoff" in r.json()["error"]["message"]
    r = client.post("/assessments", json={**REVIEW, "mode": "replay", "cutoff": "2024-05-10T15:00:00Z"})
    assert r.status_code == 400 and "cutoff позже начала ВКД" in r.json()["error"]["message"]
    r = client.post("/assessments", json={**REVIEW, "constraints": {"min_sunlit_fraction": 1.01}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation"
    r = client.post("/jobs", json={**REVIEW, "constraints": {"min_sunlit_fraction": 1.01}})
    assert r.status_code == 400


def test_illumination_constraint_excludes_windows_starting_in_shadow(client: TestClient) -> None:
    # По эфемериде фикстуры 10 мая 2024 тень 13:56–14:02 и 15:29–15:34 (сутки с бета-углом около 69.5°,
    # тени по 5–7 мин): запрошенное окно 14:00 и кандидат 15:30 стартуют в тени, остальные кандидаты на свету.
    body = client.post("/assessments", json={**REVIEW, "constraints": {"start_in_daylight": True}}).json()
    assert body["request"]["constraints"] == {"min_sunlit_fraction": None, "start_in_daylight": True}
    assert body["requested_window"]["excluded_reason"] == "ограничение освещённости: старт в тени"
    assert "illumination" not in body["requested_window"]["per_line"]
    by_start = {w["window"]["start"]: w for w in body["windows"]}
    assert by_start["2024-05-10T14:00:00Z"]["excluded_reason"] == "ограничение освещённости: старт в тени"
    assert by_start["2024-05-10T15:30:00Z"]["excluded_reason"] == "ограничение освещённости: старт в тени"
    assert by_start["2024-05-10T13:00:00Z"]["excluded_reason"] is None
    # D49: запрошенное 14:00 исключено, берётся ближайшее к нему допустимое окно; 13:45 и 14:15 равноудалены,
    # при равенстве раньше начинающееся.
    assert body["recommendation"]["preferred"]["start"] == "2024-05-10T13:45:00Z"
    assert "запрошенное окно исключено; выбрано ближайшее к нему" in body["recommendation"]["reason"]
    assert any(r.startswith("ограничение освещённости: старт на свету") for r in body["rules"])
    illumination = next(line for line in body["lines"] if line["line"] == "illumination")
    assert illumination["mechanism"] == "work_conditions" and illumination["availability"] == "available"
    assert 0.9 <= illumination["metrics"]["sunlit_fraction"] <= 1.0
    assert 0.0 < illumination["metrics"]["longest_shadow_minutes"] <= 10.0
    assert all(iv["kind"] == "computed" for iv in illumination["intervals"])
    assert {p["source"] for iv in illumination["intervals"] for p in iv["provenance"]} == {"nasa_oem"}


def test_min_sunlit_fraction_above_the_day_excludes_everything(client: TestClient) -> None:
    # Доля света в любом шестичасовом окне 10 мая ниже 0.99 (сутки с тенями по 5–7 мин каждый виток).
    body = client.post("/assessments", json={**REVIEW, "constraints": {"min_sunlit_fraction": 0.99}}).json()
    assert body["recommendation"]["insufficient"] is True
    for w in [*body["windows"], body["requested_window"]]:
        assert w["excluded_reason"] is not None and "доля света 0.9" in w["excluded_reason"]
        assert "< 0.99" in w["excluded_reason"] and "illumination" not in w["per_line"]
    assert any("доля света в окне не ниже 0.99" in r for r in body["rules"])
    plain = client.post("/assessments", json=REVIEW).json()
    assert not any("освещённости" in r for r in plain["rules"])


def test_health_and_static(client: TestClient) -> None:
    assert client.get("/health").status_code == 200
    page = client.get("/")
    assert page.status_code == 200 and "<html" in page.text.lower()


def test_export_matches_api(client: TestClient) -> None:
    body = client.post("/assessments", json=REVIEW).json()
    z = client.get(f"/assessments/{body['id']}/export")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(z.content))
    assert json.loads(zf.read("result.json")) == body


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_access_geometry_is_informational_and_does_not_change_the_recommendation(client: TestClient) -> None:
    # D61: линия геометрии есть в результате, в сравнении окон не участвует, поле geometry согласовано с линией.
    body = client.post("/assessments", json=REVIEW).json()
    line = next(a for a in body["lines"] if a["line"] == "proton_access_geometry")
    assert line["availability"] == "available" and line["metrics"]["kp"] == 9.0
    assert all("proton_access_geometry" not in w["per_line"] for w in body["windows"])
    assert not any("proton_access_geometry" in rule for rule in body["rules"] if rule.startswith("в сравнении"))
    geometry = body["geometry"]
    assert geometry["kp"] == 9.0 and "D61" in geometry["note"] and "информационно" in geometry["note"]
    start, end = body["requested_window"]["window"]["start"], body["requested_window"]["window"]["end"]
    inside = sum(
        (min(parse(iv["end"]), parse(end)) - max(parse(iv["start"]), parse(start))).total_seconds() / 60.0
        for iv in line["intervals"]
        if iv["level"] == "elevated" and iv["start"] < end and iv["end"] > start
    )
    assert geometry["requested_minutes"] == round(inside, 1)
    if geometry["safest"] is not None:
        allowed = [body["recommendation"]["preferred"], *body["recommendation"]["equivalents"]]
        assert geometry["safest"] in allowed and geometry["safest_minutes"] <= geometry["requested_minutes"]


BAD_IDS = ("0123456789AB", "0123456789ab0", "01234567890", "неттакого")


def test_a_briefing_file_name_is_not_an_assessment_id(client: TestClient, settings: Settings) -> None:
    """Рядом с расчётом лежит файл сводки `<id>.briefing.summary.ru.json`: его имя не идентификатор расчёта."""
    ident = client.post("/assessments", json=REVIEW).json()["id"]
    assert client.get(f"/assessments/{ident}/briefing").status_code == 200
    summary = f"{ident}.briefing.summary.ru"
    assert (settings.assessments_root / f"{summary}.json").exists()
    for path in (f"/assessments/{summary}", f"/assessments/{summary}/export", f"/assessments/{summary}/briefing"):
        r = client.get(path)
        assert r.status_code == 404, f"{path}: {r.status_code}"
        assert r.json()["error"]["code"] == "not_found"
    ask = client.post(f"/assessments/{summary}/briefing/ask", json={"text": "Можно выйти?"})
    assert ask.status_code == 404 and ask.json()["error"]["code"] == "not_found"


def test_an_id_outside_the_accepted_form_is_not_found(client: TestClient) -> None:
    for bad in BAD_IDS:
        r = client.get(f"/assessments/{bad}")
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found", bad
        assert client.get(f"/assessments/{bad}/export").status_code == 404


def test_a_file_that_is_not_an_assessment_is_not_found(client: TestClient, settings: Settings) -> None:
    """Битый файл с подходящим именем это отсутствие расчёта, а не ошибка сервера."""
    settings.assessments_root.mkdir(parents=True, exist_ok=True)
    (settings.assessments_root / "0123456789ab.json").write_text("{не json", encoding="utf-8")
    (settings.assessments_root / "ffffffffffff.json").write_text('{"id": "ffffffffffff"}', encoding="utf-8")
    for ident in ("0123456789ab", "ffffffffffff"):
        assert client.get(f"/assessments/{ident}").status_code == 404
        assert client.get(f"/assessments/{ident}/export").status_code == 404
        assert client.get(f"/assessments/{ident}/briefing").status_code == 404
        assert client.post(f"/assessments/{ident}/briefing/ask", json={"text": "Можно выйти?"}).status_code == 404


def test_broken_assessment_file_is_left_out_of_the_list_and_audio_needs_a_valid_id(
    client: TestClient, settings: Settings
) -> None:
    good = client.post("/assessments", json=REVIEW).json()["id"]
    (settings.assessments_root / "0123456789ab.json").write_text("{не json", encoding="utf-8")
    listed = client.get("/assessments")
    assert listed.status_code == 200 and [item["id"] for item in listed.json()] == [good]
    assert client.get("/assessments/0123456789ab").status_code == 404
    assert client.get("/assessments/..%2F..%2Fsecret/briefing/audio").status_code == 404
    assert client.get(f"/assessments/{good}.briefing.summary.ru/briefing/audio").status_code == 404
