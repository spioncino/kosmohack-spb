import io
import json
import re
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from evarisk.api.app import create_app
from evarisk.api.schemas import AssessmentRequest
from evarisk.config import Settings
from evarisk.orbit.trajectory import Trajectory, from_oem
from evarisk.parsers.donki import parse_notifications
from evarisk.parsers.swpc_text import issued_at
from evarisk.replay.verify import observe
from evarisk.store.asof import AsOf
from evarisk.store.raw import RawStore
from evarisk.store.views import oem_for, provenance_of
from evarisk.types import Window
from evarisk.windows.compare import candidates, load_window_rules

pytestmark = pytest.mark.e2e

F = datetime(2026, 9, 18, tzinfo=UTC)
FIX = Path("tests/fixtures/swpc_archive")
# Эфемерида фикстуры создана 2024-05-10T18:51:53Z, поэтому отсечение позже неё: 19:00Z.
CUTOFF = "2024-05-10T19:00:00Z"
REPLAY = {
    "mode": "replay",
    "cutoff": CUTOFF,
    "eva_start": "2024-05-11T04:00:00Z",
    "duration_hours": 6,
    "search_start": "2024-05-11T00:00:00Z",
    "search_end": "2024-05-11T14:00:00Z",
}
SEARCH_MINUTES = 14 * 60
# Официальный список: событие 11 мая 02:10–09:10, 116 pfu; DONKI: поток >100 MeV выше 1 pfu с 02:10.
# Критический уровень возможен не раньше 02:10 (430 мин после отсечения) и по risk/README.md начинается
# не позже 04:39 (579 мин).
LEAD_MIN, LEAD_MAX = 430.0, 579.0


def put_swpc(store: RawStore, name: str, product: str, version: str) -> None:
    text = (FIX / name).read_text(encoding="utf-8")
    store.put(
        source="swpc_archive",
        product=product,
        url=f"https://x.invalid/{name}",
        content=text.encode("utf-8"),
        fetched_at=F,
        published_at=issued_at(text),
        published_at_kind="exact",
        content_type="text/plain",
        data_version=version,
    )


def extend(store: RawStore) -> None:
    store.put(
        source="goes_sgps",
        product="sgps_l2_avg5m",
        url="https://x.invalid/g20240511.nc",
        content=Path("tests/fixtures/goes/sci_sgps-l2-avg5m_g18_d20240511_v3-0-2.nc").read_bytes(),
        fetched_at=F,
        published_at=datetime(2024, 5, 12, 0, 5, tzinfo=UTC),
        published_at_kind="derived",
        content_type="application/x-netcdf",
        data_version="reprocessed v3-0-2",
    )
    put_swpc(store, "202405100030three_day_forecast.txt", "3day_forecast", "issue 0030")
    put_swpc(store, "202405101230three_day_forecast.txt", "3day_forecast", "issue 1230")
    put_swpc(store, "20240509geomag_forecast.txt", "geomag_forecast", "daily")
    put_swpc(store, "20240510daypre.txt", "daypre", "daily")
    donki = Path("tests/fixtures/donki/notifications_2024-05-09_2024-05-12.json").read_bytes()
    store.put(
        source="donki",
        product="notifications",
        url="https://x.invalid/donki.json",
        content=donki,
        fetched_at=F,
        published_at=max(n.issued_at for n in parse_notifications(donki)),
        published_at_kind="derived",
        content_type="application/json",
        data_version="notifications",
    )


@pytest.fixture
def full_client(settings: Settings, engine: Engine) -> TestClient:
    extend(RawStore(settings.raw_root))
    return TestClient(create_app(settings, engine=engine))


def test_replay_carries_verification_against_the_fact(full_client: TestClient) -> None:
    r = full_client.post("/assessments", json=REPLAY)
    assert r.status_code == 200, r.text
    body = r.json()
    v = body["verification"]
    assert v is not None and v["truth_asof"] is None and v["line"] == "sep_protons"
    counts = [
        v[k] for k in ("hit_minutes", "miss_minutes", "false_alarm_minutes", "quiet_minutes", "unverified_minutes")
    ]
    assert all(c >= 0 for c in counts) and sum(counts) == SEARCH_MINUTES
    assert v["observed_availability"] == "available"
    observed_levels = {iv["level"] for iv in v["observed"]}
    assert "critical" in observed_levels
    assert all(iv["kind"] == "computed" for iv in v["observed"])
    predicted_levels = {iv["level"] for iv in body["lines"][0]["intervals"]}
    # D47: high на минутах доступа при прогнозе 55 %: витки доступа известны по траектории и прогнозу Kp,
    # поэтому часть минут факта ≥ high предсказана; витки, которые факт получает при Kp definitive, пропущены,
    # а витки только по прогнозному Kp это ложные тревоги.
    assert predicted_levels == {"elevated", "high"} and v["predicted"] == body["lines"][0]["intervals"]
    high_minutes = sum(
        (datetime.fromisoformat(i["end"]) - datetime.fromisoformat(i["start"])).total_seconds() / 60
        for i in v["predicted"]
        if i["level"] == "high"
    )
    assert high_minutes == body["lines"][0]["metrics"]["access_minutes"]
    assert v["hit_minutes"] > 0 and v["miss_minutes"] > 0
    assert v["hit_minutes"] + v["false_alarm_minutes"] == high_minutes
    assert v["lead_time_minutes"] is not None and LEAD_MIN <= v["lead_time_minutes"] <= LEAD_MAX
    assert v["first_observed_alarm"] is not None and v["first_predicted_warning"] == "2024-05-11T00:00:00Z"


def test_verification_does_not_change_the_recommendation(full_client: TestClient) -> None:
    body = full_client.post("/assessments", json=REPLAY).json()
    v = body["verification"]
    # D47 и D48: витки доступа по прогнозу лежат до 07:52 (risk/README.md), запрошенное окно 04:00–10:00 содержит
    # три из них (18 мин, больше шага сетки), поэтому рекомендация уходит на окно без открытых минут.
    preferred = body["recommendation"]["preferred"]
    assert preferred["start"] >= "2024-05-11T06:30:00Z" and "запрошенное окно хуже" in body["recommendation"]["reason"]
    # Факт содержит critical до 07:52, поэтому окно по факту начинается не раньше 08:00 или его нет.
    observed = v["observed_preferred"]
    assert observed is None or observed["start"] >= "2024-05-11T08:00:00Z"
    assert v["window_choice_stable"] == (observed == preferred)
    assert "D5" in v["note"]
    saved = full_client.get(f"/assessments/{body['id']}")
    assert saved.status_code == 200 and saved.json()["verification"] == v


def test_replay_after_donki_100_mev_alert_hits_the_fact(full_client: TestClient) -> None:
    # DONKI 20240511-AL-006 (02:30Z): поток >100 MeV выше 1 pfu с 02:10; отсечение 03:00 позже сообщения.
    body = full_client.post("/assessments", json={**REPLAY, "cutoff": "2024-05-11T03:00:00Z"}).json()
    v = body["verification"]
    sep = body["lines"][0]
    assert sep["line"] == "sep_protons" and sep["availability"] == "available"
    critical = [iv for iv in sep["intervals"] if iv["level"] == "critical"]
    assert critical and all("DONKI: ≥100 MeV > 1 pfu с 2024-05-11 02:10" in iv["note"] for iv in critical)
    assert all("D44" in iv["rule"] for iv in critical)
    assert v["hit_minutes"] > 0 and v["miss_minutes"] >= 0
    assert v["first_predicted_warning"] == "2024-05-11T00:00:00Z"
    # Первый факт ≥ high приходится на виток с пиком |mlat| 55.7° в 02:20: при Kp 9 по факту широта обрезания
    # 63.44° − 8.28° = 55.16° (D62), событие ≥100 MeV идёт с 02:10. Это раньше отсечения 03:00, поэтому
    # заблаговременность не определена.
    assert "2024-05-11T02:15:00Z" <= v["first_observed_alarm"] <= "2024-05-11T02:25:00Z"
    assert v["lead_time_minutes"] is None
    assert sep["metrics"]["donki_state_minutes"] > 0 and sep["metrics"]["cme_forecast_minutes"] > 0
    assert any(p["source"] == "donki" for p in critical[0]["provenance"])
    assert any("D44" in lim for lim in sep["limitations"])


def test_review_has_no_verification(full_client: TestClient) -> None:
    body = full_client.post("/assessments", json={**REPLAY, "mode": "review", "cutoff": None}).json()
    assert body["verification"] is None


def export(client: TestClient, assessment_id: str) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(client.get(f"/assessments/{assessment_id}/export").content))


def test_export_report_has_verification_section(full_client: TestClient) -> None:
    body = full_client.post("/assessments", json=REPLAY).json()
    z = export(full_client, body["id"])
    report = z.read("report.md").decode("utf-8")
    assert "## Проверка после события" in report and "Заблаговременность" in report
    assert "в расчёт и рекомендацию не входит (D41)" in report
    # Факт это тот же расчёт по всем данным, доступным сегодня, а не независимые наблюдения (D78).
    assert "тот же расчёт с теми же правилами по всем данным, доступным сегодня" in report
    assert "по наблюдениям, опубликованным позже отсечения" not in report
    for iv in body["verification"]["observed"]:
        for p in iv["provenance"]:
            assert p["raw_id"] in report


# raw_id это 24 шестнадцатеричных знака (store.raw.put): sha256 от источника, продукта, url и sha содержимого.
RAW_ID = re.compile(r"\b[0-9a-f]{24}\b")


def test_every_raw_id_of_the_report_is_listed_in_sources(full_client: TestClient) -> None:
    """Отчёт ссылается и на источники сверки: без них в sources.json отчёт ссылался бы в пустоту."""
    body = full_client.post("/assessments", json=REPLAY).json()
    z = export(full_client, body["id"])
    report = z.read("report.md").decode("utf-8")
    listed = {s["raw_id"] for s in json.loads(z.read("sources.json"))}
    mentioned = set(RAW_ID.findall(report))
    assert mentioned and mentioned <= listed
    used = {p["raw_id"] for p in body["sources_used"]}
    fact = {p["raw_id"] for iv in body["verification"]["observed"] for p in iv["provenance"]}
    assert fact - used, "факт сверки опирается на записи, которых в sources_used расчёта нет"
    assert listed == used | fact | {p["raw_id"] for iv in body["verification"]["predicted"] for p in iv["provenance"]}
    # Источник, попавший в отчёт только из сверки, помечен как не входящий в расчёт.
    for raw_id in fact - used:
        line = next(x for x in report.splitlines() if x.startswith(f"- {raw_id}:"))
        assert "только в проверке после события" in line


def _provenance_outside_verification(node: object) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    if isinstance(node, dict):
        if "raw_id" in node and "published_at" in node:
            found.append(node)
        for key, value in node.items():
            if key != "verification":
                found.extend(_provenance_outside_verification(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_provenance_outside_verification(value))
    return found


@pytest.mark.parametrize("cutoff", ["2024-05-10T19:00:00Z", "2024-05-11T03:00:00Z"])
def test_nothing_published_after_the_cutoff_is_referenced_outside_verification(
    full_client: TestClient, cutoff: str
) -> None:
    # Файл уведомлений DONKI фикстуры доступен 12 мая, его сообщения выпущены 9–12 мая: отсечение применяется к
    # сообщениям, и провенанс результата несёт время выпуска сообщения, а не файла.
    body = full_client.post("/assessments", json={**REPLAY, "cutoff": cutoff}).json()
    provenance = _provenance_outside_verification(body)
    assert provenance and any(p["source"] == "donki" for p in provenance)
    late = [p for p in provenance if p["published_at"] is None or str(p["published_at"]) > cutoff]
    assert late == []


def test_replay_compares_three_baselines_on_the_same_cutoff(full_client: TestClient) -> None:
    body = full_client.post("/assessments", json=REPLAY).json()
    v = body["verification"]
    baselines = {b["name"]: b for b in v["baselines"]}
    assert list(baselines) == ["persistence", "single_alert", "swpc_outlook"]
    # Бейзлайны видят только данные до отсечения, как и расчёт.
    for b in v["baselines"]:
        assert all(p["published_at"] is not None and p["published_at"] <= CUTOFF for p in b["provenance"])
    # Суточный файл GOES за 10 мая доступен 11 мая 00:05, файла за 9 мая в фикстурах нет: у персистентности
    # нет отсчёта, это «нет данных» и отказ от выбора окна, а не спокойная обстановка.
    persistence = baselines["persistence"]
    assert persistence["level"] == "unknown" and persistence["preferred"] is None
    assert persistence["insufficient"] is True and persistence["preferred_exposure_minutes"] is None
    # DONKI 20240510-AL-005 (14:12): поток >10 MeV выше 10 pfu; 3-day forecast 12:30: S1+ 55 % на 11 мая.
    assert baselines["single_alert"]["level"] == "elevated" and "20240510-AL-005" in baselines["single_alert"]["basis"]
    assert baselines["swpc_outlook"]["level"] == "elevated" and "55 %" in baselines["swpc_outlook"]["basis"]
    # Один уровень на весь период не различает окна: оба бейзлайна оставляют запрошенное окно, и минуты
    # экспозиции по факту в нём те же, что в запрошенном окне.
    for name in ("single_alert", "swpc_outlook"):
        b = baselines[name]
        assert b["preferred"] == {"start": REPLAY["eva_start"], "end": "2024-05-11T10:00:00Z"}
        assert b["preferred_exposure_minutes"] == v["requested_exposure_minutes"]
        # Уровень elevated не называет экспозицию: все минуты экспозиции факта это пропуски.
        assert b["exposure_hit_minutes"] == 0 and b["exposure_false_alarm_minutes"] == 0
        assert b["exposure_miss_minutes"] == v["exposure_hit_minutes"] + v["exposure_miss_minutes"]
    assert v["requested_exposure_minutes"] > 0 and v["preferred_exposure_minutes"] is not None
    assert "D41" in v["note"]


def test_export_report_has_baseline_table(full_client: TestClient) -> None:
    body = full_client.post("/assessments", json=REPLAY).json()
    r = full_client.get(f"/assessments/{body['id']}/export")
    report = zipfile.ZipFile(io.BytesIO(r.content)).read("report.md").decode("utf-8")
    assert "### Сравнение с простыми подходами" in report
    for title in ("наш расчёт", "персистентность", "единственное предупреждение", "прогноз SWPC без орбиты"):
        assert f"| {title} |" in report
    sources = json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("sources.json"))
    listed = {s["raw_id"] for s in sources}
    assert all(p["raw_id"] in listed for b in body["verification"]["baselines"] for p in b["provenance"])


MINUTE = timedelta(minutes=1)
# Старт 13:15 11 мая приходится на проход тени МКС по траектории факта: это предусловие проверяется в тесте.
DAYLIGHT_START = {
    **REPLAY,
    "eva_start": "2024-05-11T13:15:00Z",
    "duration_hours": 1,
    "search_start": "2024-05-11T09:00:00Z",
    "search_end": "2024-05-11T15:00:00Z",
    "constraints": {"start_in_daylight": True},
}
# Период обращения МКС на высоте около 415 км 2π·sqrt(a³/μ) ≈ 93 мин: окно 2 ч длиннее витка, и при проходе тени
# на каждом витке в любом окне есть минуты тени. Доля света не ниже 1.0 исключает все окна по одному правилу.
FULL_SUN = {**REPLAY, "duration_hours": 2, "constraints": {"min_sunlit_fraction": 1.0}}


def _truth_trajectory(settings: Settings, req: AssessmentRequest) -> Trajectory:
    """Траектория факта тем же путём, что у сверки: эфемерида по `AsOf(store, None)`, реконструкция."""
    span = Window(start=req.search_start, end=req.search_end)
    found = oem_for(AsOf(RawStore(settings.raw_root), None), span)
    assert found is not None
    eph, rec = found
    return from_oem(eph, provenance_of(rec), span, is_reconstruction=True)


def _sunlit(trajectory: Trajectory, start: datetime, end: datetime) -> list[bool]:
    """Флаги света траектории на минутах [start, end)."""
    return [bool(trajectory.sunlit[i]) for i, t in enumerate(trajectory.times) if start <= t < end]


def _window(value: dict[str, str] | None) -> Window | None:
    return None if value is None else Window.model_validate(value)


def test_fact_ranks_windows_with_the_start_in_daylight_constraint(full_client: TestClient, settings: Settings) -> None:
    req = AssessmentRequest.model_validate(DAYLIGHT_START)
    trajectory = _truth_trajectory(settings, req)
    assert _sunlit(trajectory, req.eva_start, req.eva_start + MINUTE) == [False]
    body = full_client.post("/assessments", json=DAYLIGHT_START).json()
    v = body["verification"]
    assert "ограничение освещённости: старт в тени" in body["requested_window"]["excluded_reason"]
    predicted = _window(body["recommendation"]["preferred"])
    assert predicted is None or _sunlit(trajectory, predicted.start, predicted.start + MINUTE) == [True]
    # D43: окно со стартом в тени исключается и в расчёте, и в факте. Предпочтительное окно факта не запрошенное
    # и начинается на свету по траектории факта.
    observed = _window(v["observed_preferred"])
    assert observed != _window(body["requested_window"]["window"])
    assert observed is None or _sunlit(trajectory, observed.start, observed.start + MINUTE) == [True]
    truth = observe(req, RawStore(settings.raw_root))
    assert truth is not None
    for s in truth.windows:
        if _sunlit(trajectory, s.window.start, s.window.start + MINUTE) == [False]:
            assert "ограничение освещённости: старт в тени" in (s.excluded_reason or "")
    # Устойчивость выбора сравнивает окно расчёта с предпочтительным и равнозначными окнами факта, оценёнными по
    # тому же ограничению; все они начинаются на свету.
    rec = truth.recommendation
    assert observed == rec.preferred
    for w in rec.equivalents:
        assert _sunlit(trajectory, w.start, w.start + MINUTE) == [True]
    assert v["window_choice_stable"] == (predicted == rec.preferred or predicted in rec.equivalents)


def test_same_sunlit_fraction_rule_leaves_both_choices_empty(full_client: TestClient, settings: Settings) -> None:
    req = AssessmentRequest.model_validate(FULL_SUN)
    trajectory = _truth_trajectory(settings, req)
    duration = timedelta(hours=req.duration_hours)
    windows = candidates(req.search_start, req.search_end, duration, load_window_rules())
    requested = Window(start=req.eva_start, end=req.eva_start + duration)
    assert all(False in _sunlit(trajectory, w.start, w.end) for w in [*windows, requested])
    body = full_client.post("/assessments", json=FULL_SUN).json()
    v = body["verification"]
    assert body["recommendation"]["preferred"] is None and body["recommendation"]["insufficient"] is True
    # По факту действует то же правило: окон со всеми минутами на свету нет, выбора нет и по факту. Оба выбора
    # пусты, это совпадение (D41), а не расхождение прогноза с фактом.
    assert v["observed_preferred"] is None and v["observed_insufficient"] is True
    assert v["window_choice_stable"] is True
    assert "в обоих случаях оснований для выбора нет" in v["note"]
    truth = observe(req, RawStore(settings.raw_root))
    assert truth is not None
    assert all("ограничение освещённости: доля света" in (s.excluded_reason or "") for s in truth.windows)
    # Бейзлайны ранжируют окна с ограничениями запроса и сверяются с тем же фактом.
    assert v["baselines"] and all(b["preferred"] is None and b["window_choice_stable"] for b in v["baselines"])
