"""Приёмочные проверки против поднятого API: десять сценариев, отчёт в docs/ACCEPTANCE.md.

Запуск: scripts/dev_server.sh restart (API и воркер), затем uv run python scripts/acceptance_checks.py.
Расчёты идут заданиями POST /jobs с опросом GET /jobs/{id}, как на странице: воркер докачивает архив периода,
поэтому проверки проходят и на свежем клоне с пустым data/. Синхронный POST /assessments вызывается только там,
где проверяется его отказ (409).
Проверка деградации меняет состояния источников и возвращает их в active; для неё можно указать отдельный
экземпляр API с собственной базой (--degradation-api), чтобы не мешать основному.
"""

import argparse
import io
import json
import sys
import time
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

TIMEOUT_SECONDS = 900
# Задание с докачкой архива на холодной машине идёт минуты; опрос раз в 2 с, как на странице.
JOB_TIMEOUT_SECONDS = 1200
JOB_POLL_SECONDS = 2.0
VOLATILE_KEYS = ("id", "created_at", "asof")
MAY_11 = {
    "eva_start": "2024-05-11T04:00:00Z",
    "duration_hours": 6.0,
    "search_start": "2024-05-11T00:00:00Z",
    "search_end": "2024-05-12T00:00:00Z",
}
# Событие 8 июня 2024 при отсечении 00:30: тот же запрос, что docs/experiments/event_2024-06-08_2024-06-08T0030Z.json.
JUNE_8: dict[str, Any] = {
    "mode": "replay",
    "cutoff": "2024-06-08T00:30:00Z",
    "eva_start": "2024-06-08T06:00:00Z",
    "duration_hours": 6.0,
    "search_start": "2024-06-08T00:00:00Z",
    "search_end": "2024-06-09T00:00:00Z",
}
JUNE_8_MISSED_MINUTES = 32.0
DANGER_LEVELS = ("high", "critical")
# Слова, которыми текст рекомендации заявлял бы спокойную обстановку или полноту данных.
CALM_CLAIMS = (
    "спокойн",
    "спокойств",
    "благоприят",
    "безопас",
    "не опасн",
    "полные данные",
    "данные полн",
    "пробелов нет",
    "без пробелов",
)
# Оговорка D64 отрицает спокойствие и в поиск заявлений не входит.
UNMEASURED_DISCLAIMER = "это не измеренное спокойствие"


@dataclass
class Check:
    title: str
    expectation: str
    passed: bool = True
    lines: list[str] = field(default_factory=list)

    def note(self, text: str) -> None:
        self.lines.append(text)

    def require(self, condition: bool, text: str) -> None:
        self.lines.append(f"{'верно' if condition else 'НЕ ВЫПОЛНЕНО'}: {text}")
        self.passed = self.passed and condition


def call(api: str, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    req = Request(api + path, data=data, headers={"content-type": "application/json"}, method=method)
    try:
        with urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
            status = response.status
    except HTTPError as error:
        raw, status = error.read(), error.code
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


def wait_job(api: str, job_id: str) -> dict[str, Any]:
    """Опрашивает GET /jobs/{id} до done или failed; без воркера задание не сдвинется, и ошибка это называет."""
    deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
    job: dict[str, Any] = {}
    while time.monotonic() < deadline:
        time.sleep(JOB_POLL_SECONDS)
        _, job = call(api, "GET", f"/jobs/{job_id}")
        if job["status"] in ("done", "failed"):
            return job
    raise RuntimeError(
        f"задание {job_id} не завершилось за {JOB_TIMEOUT_SECONDS} с (статус {job.get('status')}); воркер запущен?"
    )


def run_job(api: str, body: dict[str, Any]) -> dict[str, Any]:
    status, created = call(api, "POST", "/jobs", body)
    if status != 202 or not isinstance(created, dict):
        raise RuntimeError(f"POST /jobs {status}: {created!r}")
    return wait_job(api, created["job_id"])


def assess(api: str, body: dict[str, Any]) -> dict[str, Any]:
    """Расчёт заданием, как на странице: воркер докачивает архив периода, если его нет в хранилище."""
    job = run_job(api, body)
    if job["status"] != "done":
        raise RuntimeError(f"задание расчёта {job['id']} не выполнено: {job['error']}")
    status, payload = call(api, "GET", f"/assessments/{job['assessment_id']}")
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"GET /assessments/{job['assessment_id']} {status}: {payload!r}")
    return payload


def fetched_note(result: dict[str, Any]) -> str:
    fetched = [lim for lim in result["limitations"] if "докачан по запросу" in lim]
    return fetched[0] if fetched else "не понадобилась, период уже в хранилище"


def provenance_outside_verification(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if "raw_id" in node and "published_at" in node:
            found.append(node)
        for key, value in node.items():
            if key != "verification":
                found.extend(provenance_outside_verification(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(provenance_outside_verification(value))
    return found


def later_than(result: dict[str, Any], cutoff: str) -> list[dict[str, Any]]:
    return [
        p for p in provenance_outside_verification(result) if p["published_at"] is None or p["published_at"] > cutoff
    ]


def levels(result: dict[str, Any], line: str) -> set[str]:
    found = next(a for a in result["lines"] if a["line"] == line)
    return {iv["level"] for iv in found["intervals"]}


def window_text(window: dict[str, str] | None) -> str:
    return "нет" if window is None else f"{window['start'][5:16]}–{window['end'][11:16]}"


def summary(result: dict[str, Any]) -> str:
    rec = result["recommendation"]
    excluded = sum(1 for w in result["windows"] if w["excluded_reason"])
    return (
        f"окно {window_text(rec['preferred'])}, равнозначных {len(rec['equivalents'])}, исключено {excluded} из "
        f"{len(result['windows'])}, версия {result['algorithm_version']}"
    )


def stable(result: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in result.items() if k not in VOLATILE_KEYS}


def check_leak(api: str) -> Check:
    c = Check(
        "1. Утечка будущего",
        "отсечение 9 мая 18:00 не знает о событии; 23:00 знает; вне блока проверки нет провенанса позже отсечения",
    )
    early = assess(api, {"mode": "replay", "cutoff": "2024-05-09T18:00:00Z", **MAY_11})
    late = assess(api, {"mode": "replay", "cutoff": "2024-05-09T23:00:00Z", **MAY_11})
    c.note(f"A, отсечение 18:00: уровни SEP {sorted(levels(early, 'sep_protons'))}; {summary(early)}")
    c.note(f"B, отсечение 23:00: уровни SEP {sorted(levels(late, 'sep_protons'))}; {summary(late)}")
    c.require(levels(early, "sep_protons") <= {"none", "unknown"}, "A не показывает уровней выше фона")
    c.require(bool(levels(late, "sep_protons") & {"elevated", "high"}), "B показывает предупреждение")
    sources_late = {p["source"] for p in provenance_outside_verification(late)}
    c.require("donki" in sources_late, "B ссылается на сообщения DONKI")
    for name, result in (("A", early), ("B", late)):
        cutoff = result["request"]["cutoff"]
        found = later_than(result, cutoff)
        total = len(provenance_outside_verification(result))
        c.require(not found, f"{name}: провенансов позже отсечения {len(found)} из {total}")
    reasons = [lim for lim in late["lines"][0]["limitations"] if "вероятность протонного события" in lim]
    c.note(f"B, основание уровня: {reasons[0] if reasons else 'не найдено'}")
    return c


def check_monotonic(api: str) -> Check:
    c = Check(
        "2. Монотонность по отсечению",
        "с ростом отсечения выпуски одного продукта не стареют, позже отсечения ничего нет, уверенность не падает",
    )
    grades = {"low": 0, "medium": 1, "high": 2}
    previous: dict[tuple[str, str], str] = {}
    previous_grade = -1
    for cutoff in ("2024-05-09T00:00:00Z", "2024-05-10T00:30:00Z", "2024-05-10T12:30:00Z", "2024-05-11T00:00:00Z"):
        result = assess(api, {"mode": "replay", "cutoff": cutoff, **MAY_11})
        newest: dict[tuple[str, str], str] = {}
        for p in provenance_outside_verification(result):
            key = (p["source"], p["product"])
            newest[key] = max(newest.get(key, ""), p["published_at"] or "")
        grade = result["lines"][0]["confidence"]["grade"]
        c.note(
            f"отсечение {cutoff[5:16]}: уровни SEP {sorted(levels(result, 'sep_protons'))}, уверенность {grade}, "
            f"продуктов {len(newest)}; {summary(result)}"
        )
        c.require(not later_than(result, cutoff), f"{cutoff[5:16]}: нет провенанса позже отсечения")
        older = [k for k, stamp in newest.items() if k in previous and stamp < previous[k]]
        c.require(not older, f"{cutoff[5:16]}: ни один продукт не откатился к более старому выпуску ({older})")
        gone = sorted(f"{s}/{p}" for s, p in set(previous) - set(newest))
        if gone:
            c.note(f"  перестали использоваться (заменены более свежим продуктом той же величины): {', '.join(gone)}")
        c.require(grades[grade] >= previous_grade, f"{cutoff[5:16]}: уверенность не упала")
        previous, previous_grade = newest, grades[grade]
    return c


def check_quiet_day(api: str) -> Check:
    c = Check("3. Спокойный период", "20 июня 2024: фон по протонам, перенос не предлагается, штрафы малы")
    body = {
        "mode": "review",
        "eva_start": "2024-06-20T06:00:00Z",
        "duration_hours": 6.0,
        "search_start": "2024-06-20T00:00:00Z",
        "search_end": "2024-06-21T00:00:00Z",
    }
    quiet = assess(api, body)
    storm = assess(api, {"mode": "review", **MAY_11})
    c.note(f"20 июня: уровни SEP {sorted(levels(quiet, 'sep_protons'))}; {summary(quiet)}")
    c.note(f"20 июня, причина: {quiet['recommendation']['reason']}")
    c.note(f"11 мая для сравнения: уровни SEP {sorted(levels(storm, 'sep_protons'))}; {summary(storm)}")
    c.note(f"20 июня, докачка: {fetched_note(quiet)}")
    c.require(levels(quiet, "sep_protons") == {"none"}, "линия протонов в спокойный день целиком фоновая")
    c.require("перенос не улучшает" in quiet["recommendation"]["reason"], "перенос не предлагается")
    preferred = quiet["recommendation"]["preferred"]
    if preferred is None:
        c.require(False, "предпочтительным осталось запрошенное окно: рекомендованного окна нет, см. причину выше")
    else:
        c.require(preferred["start"] == body["eva_start"], "предпочтительным осталось запрошенное окно")
    excluded_storm = sum(1 for w in storm["windows"] if w["excluded_reason"])
    c.require(
        excluded_storm > 0, f"в день события исключено окон {excluded_storm}, в спокойный день 0: линия различает дни"
    )
    return c


def check_degradation(api: str) -> Check:
    c = Check(
        "4. Деградация источников",
        "отключённый источник даёт «нет данных» и отказ от рекомендации, а не благоприятную обстановку",
    )
    body = {"mode": "review", **MAY_11}
    _, sources = call(api, "GET", "/sources")
    names = [s["name"] for s in sources]
    try:
        call(api, "POST", "/sources/goes_sgps/state", {"state": "disabled", "reason": "приёмочная проверка"})
        degraded = assess(api, body)
        availability = degraded["data_availability"]["sep_protons"]
        c.note(f"goes_sgps отключён: линия протонов {availability}; {summary(degraded)}")
        c.note(f"причина: {degraded['recommendation']['reason'][:220]}")
        c.require(availability == "missing", "линия протонов без источника помечена missing")
        c.require(degraded["recommendation"]["insufficient"] is True, "рекомендации нет: оснований недостаточно")
        c.require(
            any("отключены пользователем" in lim for lim in degraded["limitations"]),
            "отключение названо в ограничениях результата",
        )
        for name in names:
            call(api, "POST", f"/sources/{name}/state", {"state": "disabled", "reason": "приёмочная проверка"})
        status, payload = call(api, "POST", "/assessments", body)
        message = payload["error"]["message"] if isinstance(payload, dict) and "error" in payload else str(payload)
        c.note(f"все источники отключены: HTTP {status}, «{message}»")
        c.require(status == 409, "без источников сервис отвечает 409 с текстом и ничего не рекомендует")
    finally:
        for name in names:
            call(api, "POST", f"/sources/{name}/state", {"state": "active", "reason": "приёмочная проверка окончена"})
    restored = assess(api, body)
    c.require(restored["data_availability"]["sep_protons"] == "available", "после включения линия снова available")
    return c


def check_boundaries(api: str) -> Check:
    c = Check(
        "5. Граничные даты",
        "края диапазона считаются; период вне загруженного архива докачивается заданием; "
        "дата без архива даёт понятный отказ",
    )
    cases = [
        ("30 июня 23:00, 8 ч, поиск 24 ч", "2024-06-30T23:00:00Z", 8.0, "2024-06-30T23:00:00Z", "2024-07-01T23:00:00Z"),
        ("1 мая 00:00, 1 ч", "2024-05-01T00:00:00Z", 1.0, "2024-05-01T00:00:00Z", "2024-05-01T06:00:00Z"),
    ]
    for title, eva, hours, start, end in cases:
        body = {"mode": "review", "eva_start": eva, "duration_hours": hours, "search_start": start, "search_end": end}
        job = run_job(api, body)
        c.require(job["status"] == "done", f"{title}: задание {job['status']} {job['error'] or ''}")
        if job["status"] == "done":
            _, payload = call(api, "GET", f"/assessments/{job['assessment_id']}")
            c.note(f"  доступность линий {payload['data_availability']}; {summary(payload)}")
            c.note(f"  докачка: {fetched_note(payload)}")
    no_archive = {
        "mode": "review",
        "eva_start": "2019-07-15T04:00:00Z",
        "duration_hours": 6.0,
        "search_start": "2019-07-15T00:00:00Z",
        "search_end": "2019-07-15T14:00:00Z",
    }
    status, payload = call(api, "POST", "/assessments", no_archive)
    message = payload["error"]["message"] if isinstance(payload, dict) and "error" in payload else str(payload)
    c.note(f"  15 июля 2019, синхронный расчёт: HTTP {status}, «{message}»")
    c.require(status == 409, "дата без архива в хранилище: понятная ошибка клиента, не 500")
    c.require("POST /jobs" in message, "отказ называет путь с докачкой архива (D59)")
    outside = {
        "mode": "review",
        "eva_start": "2024-07-08T04:00:00Z",
        "duration_hours": 6.0,
        "search_start": "2024-07-08T00:00:00Z",
        "search_end": "2024-07-08T14:00:00Z",
    }
    status, created = call(api, "POST", "/jobs", outside)
    c.require(status == 202, f"8 июля 2024, задание за краем заранее загруженного архива принято: HTTP {status}")
    job = wait_job(api, created["job_id"])
    c.require(job["status"] == "done", f"задание завершено: {job['status']} {job['error'] or ''}")
    if job["status"] == "done":
        _, result = call(api, "GET", f"/assessments/{job['assessment_id']}")
        c.note(f"  доступность линий {result['data_availability']}; {summary(result)}")
        c.note(f"  докачка: {fetched_note(result)}")
        c.require(result["data_availability"]["sep_protons"] == "available", "линия протонов посчитана по архиву")
    return c


def check_duration(api: str) -> Check:
    c = Check(
        "6. Длительность против сдвига",
        "чем длиннее выход, тем меньше чистых окон; смена длительности это изменение плана, а не улучшение",
    )
    clean: list[int] = []
    for hours in (1.0, 4.0, 8.0):
        result = assess(api, {"mode": "review", **MAY_11, "duration_hours": hours})
        eligible = sum(1 for w in result["windows"] if not w["excluded_reason"])
        clean.append(eligible)
        c.note(f"{hours:g} ч: допустимых окон {eligible} из {len(result['windows'])}; {summary(result)}")
    c.require(clean[0] > clean[1] > clean[2], f"допустимых окон становится меньше с ростом длительности: {clean}")
    c.note(
        "страница при смене длительности показывает баннер «Изменение плана работ … это не улучшение условий» "
        "(frontend/src/lib/plan.ts, тест plan.test.ts, D52)"
    )
    return c


def check_constraints(api: str) -> Check:
    c = Check(
        "7. Исключение против штрафа", "ограничение освещённости исключает окна по названному правилу до ранжирования"
    )
    body = {"mode": "review", **MAY_11, "constraints": {"min_sunlit_fraction": 0.99, "start_in_daylight": True}}
    result = assess(api, body)
    by_light = [w for w in result["windows"] if "ограничение освещённости" in (w["excluded_reason"] or "")]
    c.note(f"{summary(result)}; по освещённости исключено {len(by_light)}")
    if by_light:
        c.note(f"пример причины: {by_light[0]['excluded_reason']}")
    c.require(bool(by_light), "есть окна, исключённые по ограничению освещённости, с текстом правила")
    c.require(
        sum(1 for r in result["rules"] if r.startswith("ограничение освещённости")) == 2,
        "оба ограничения названы в правилах ответа",
    )
    preferred = result["recommendation"]["preferred"]
    excluded_starts = {w["window"]["start"] for w in result["windows"] if w["excluded_reason"]}
    listed = [preferred, *result["recommendation"]["equivalents"]] if preferred else []
    c.require(
        all(w["start"] not in excluded_starts for w in listed),
        "исключённые окна не попадают ни в рекомендацию, ни в равнозначные",
    )
    return c


def check_reproducibility(api: str) -> Check:
    c = Check("8. Воспроизводимость", "повтор запроса, сохранённый расчёт и выгрузка совпадают")
    body = {"mode": "replay", "cutoff": "2024-05-10T12:30:00Z", **MAY_11, "search_end": "2024-05-11T14:00:00Z"}
    # Первый расчёт на пустом хранилище докачивает архив и пишет это в ограничения; сравниваются два следующих.
    warmup = assess(api, body)
    c.note(f"прогревочный расчёт, докачка: {fetched_note(warmup)}")
    first, second = assess(api, body), assess(api, body)
    c.require(stable(first) == stable(second), "два расчёта подряд совпадают во всём, кроме id и времени создания")
    _, saved = call(api, "GET", f"/assessments/{first['id']}")
    c.require(saved == first, "сохранённый расчёт равен ответу API")
    with urlopen(f"{api}/assessments/{first['id']}/export", timeout=TIMEOUT_SECONDS) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    c.require(json.loads(archive.read("result.json")) == first, "result.json в выгрузке равен ответу API")
    report = archive.read("report.md").decode("utf-8")
    c.require(first["algorithm_version"] in report, "версия алгоритма есть в report.md")
    c.note(f"версия алгоритма {first['algorithm_version']}; файлы выгрузки: {sorted(archive.namelist())}")
    return c


def check_current(api: str) -> Check:
    c = Check(
        "9. Текущий режим", "ближайшие 12 часов считаются, сближения доступны, видна свежесть, обновление работает"
    )
    start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    stamp = "%Y-%m-%dT%H:%M:%SZ"
    body = {
        "mode": "current",
        "eva_start": (start + timedelta(hours=2)).strftime(stamp),
        "duration_hours": 6.0,
        "search_start": start.strftime(stamp),
        "search_end": (start + timedelta(hours=12)).strftime(stamp),
    }
    result = assess(api, body)
    c.note(f"доступность линий {result['data_availability']}; {summary(result)}")
    c.require(result["data_availability"]["conjunctions"] == "available", "линия сближений доступна")
    c.require(result["data_availability"]["sep_protons"] == "available", "линия протонов доступна")
    _, before = call(api, "GET", "/sources")
    live_before = next(s for s in before if s["name"] == "swpc_live")
    c.note(f"swpc_live: последний успех {live_before['last_success_at']}, записей {live_before['records']}")
    c.require(
        all(s["last_success_at"] for s in before if not s["archive_only"]),
        "у каждого живого источника видно время успеха",
    )
    status, created = call(api, "POST", "/sources/swpc_live/refresh")
    c.require(status == 202, f"принудительное обновление принято: HTTP {status}")
    job = wait_job(api, created["job_id"])
    c.require(job["status"] == "done", f"задание обновления завершено: {job['status']} {job['error'] or ''}")
    _, after = call(api, "GET", "/sources")
    live_after = next(s for s in after if s["name"] == "swpc_live")
    c.require(
        (live_after["last_success_at"] or "") > (live_before["last_success_at"] or ""),
        f"время успеха обновилось: {live_after['last_success_at']}",
    )
    return c


def parse_time(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def level_at(intervals: list[dict[str, Any]], moment: datetime) -> str | None:
    for iv in intervals:
        if parse_time(iv["start"]) <= moment < parse_time(iv["end"]):
            return str(iv["level"])
    return None


def missed_minutes(verification: dict[str, Any], start: str, end: str) -> int:
    """Минуты, где факт ≥ high, а прогноз ниже high, пересчитанные по интервалам блока проверки."""
    moment, stop, missed = parse_time(start), parse_time(end), 0
    while moment < stop:
        observed = level_at(verification["observed"], moment)
        predicted = level_at(verification["predicted"], moment)
        missed += observed in DANGER_LEVELS and predicted not in DANGER_LEVELS
        moment += timedelta(minutes=1)
    return missed


def check_honest_miss(api: str) -> Check:
    c = Check(
        "10. Честный пропуск 8 июня",
        "replay с отсечением 8 июня 2024 00:30: внезапное событие прогноз не видит, но говорит, что поток протонов "
        "не измерен и уровень взят из прогноза, уверенность по протонам не высокая, спокойствия и полноты данных "
        "текст не заявляет; сверка считает пропущенные минуты ≥ high",
    )
    result = assess(api, JUNE_8)
    rec = result["recommendation"]
    explanation: list[str] = rec["explanation"]
    sep = next(a for a in result["lines"] if a["line"] == "sep_protons")
    verification = result["verification"]
    c.note(f"уровни SEP {sorted(levels(result, 'sep_protons'))}; {summary(result)}")
    kinds = sorted({iv["kind"] for iv in sep["intervals"] if parse_time(iv["start"]) >= parse_time(JUNE_8["cutoff"])})
    c.note(
        f"линия протонов после отсечения: вид интервалов {kinds}, неизмеренных минут "
        f"{sep['metrics'].get('unmeasured_minutes')}"
    )
    c.note(f"причина: {rec['reason'].split('; ')[0]}")
    unmeasured_at = next((i for i, line in enumerate(explanation) if "не измерен" in line), None)
    danger_at = next(
        (i for i, line in enumerate(explanation) if line.startswith(("Опасных минут", "Опасные минуты"))),
        len(explanation),
    )
    if unmeasured_at is not None:
        c.note(f"объяснение: «{explanation[unmeasured_at]}»")
    if danger_at < len(explanation):
        c.note(f"объяснение следующей строкой: «{explanation[danger_at]}», это счёт по прогнозу, не по измерению")
    c.require(
        levels(result, "sep_protons") <= {"none", "unknown"},
        "прогноз события не видит: уровни протонов только none и unknown",
    )
    c.require(
        "поток протонов не измерен" in rec["reason"] and "взят из внешнего прогноза" in rec["reason"],
        "причина рекомендации: поток протонов не измерен, уровень взят из внешнего прогноза",
    )
    c.require(
        unmeasured_at is not None and "уровень взят из прогноза" in explanation[unmeasured_at],
        "объяснение обычными словами: поток протонов не измерен, уровень взят из прогноза",
    )
    c.require(
        unmeasured_at is not None and unmeasured_at < danger_at,
        "оговорка о неизмеренном потоке стоит в объяснении раньше счёта опасных минут",
    )
    confidence = sep["confidence"]
    c.note(f"уверенность по протонам: {confidence['grade']}, основание: {confidence['depends_on']}")
    c.require(confidence["grade"] != "high", f"уверенность по протонам не высокая: {confidence['grade']}")
    texts = [rec["reason"].replace(UNMEASURED_DISCLAIMER, ""), *explanation]
    claims = sorted({word for text in texts for word in CALM_CLAIMS if word in text.lower()})
    c.require(not claims, f"причина и объяснение не заявляют спокойствия или полноты данных, найдено: {claims}")
    recounted = missed_minutes(verification, JUNE_8["search_start"], JUNE_8["search_end"])
    c.note(
        f"сверка: попаданий {verification['hit_minutes']:g}, пропусков {verification['miss_minutes']:g}, ложных "
        f"тревог {verification['false_alarm_minutes']:g}, пропусков экспозиции "
        f"{verification['exposure_miss_minutes']:g} мин; первый факт ≥ high {verification['first_observed_alarm']}; "
        f"пересчёт пропусков по интервалам {recounted} мин"
    )
    stable_choice = "да" if verification["window_choice_stable"] else "нет"
    c.note(
        f"окно по прогнозу {window_text(rec['preferred'])}, по факту "
        f"{window_text(verification['observed_preferred'])}, выбор окна устоял: {stable_choice}"
    )
    c.require(
        verification["miss_minutes"] == JUNE_8_MISSED_MINUTES == recounted,
        f"пропущенных минут ≥ high в сверке {JUNE_8_MISSED_MINUTES:g}, пересчёт по интервалам совпадает",
    )
    c.require(
        verification["hit_minutes"] == 0 and verification["first_predicted_warning"] is None,
        "сверка не приписывает прогнозу попаданий и предупреждений",
    )
    c.require(
        "прогноз не дал ни elevated, ни high" in verification["note"],
        "заметка сверки прямо говорит, что прогноз события не увидел",
    )
    return c


def render(checks: list[Check], api: str) -> str:
    passed = sum(1 for c in checks if c.passed)
    lines = [
        "# Приёмочные проверки",
        "",
        f"Прогон {datetime.now(UTC):%Y-%m-%d %H:%M} UTC против {api}: пройдено {passed} из {len(checks)}. "
        "Воспроизведение: `scripts/dev_server.sh restart` (API и воркер), затем "
        "`uv run python scripts/acceptance_checks.py`. Расчёты идут заданиями `POST /jobs` с докачкой архива, "
        "поэтому проверки проходят и на свежем клоне с пустым `data/`.",
        "",
    ]
    for c in checks:
        lines += [f"## {c.title}: {'пройдено' if c.passed else 'НЕ ПРОЙДЕНО'}", "", f"Ожидание: {c.expectation}.", ""]
        lines += [f"- {line}" for line in c.lines]
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Приёмочные проверки против поднятого API")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--degradation-api", default=None, help="отдельный экземпляр API для проверки 4")
    parser.add_argument("--out", type=Path, default=Path("docs/ACCEPTANCE.md"))
    parser.add_argument("--only", default="", help="номера проверок через запятую, например 1,4")
    args = parser.parse_args()
    runners = [
        check_leak,
        check_monotonic,
        check_quiet_day,
        check_degradation,
        check_boundaries,
        check_duration,
        check_constraints,
        check_reproducibility,
        check_current,
        check_honest_miss,
    ]
    wanted = {int(n) for n in args.only.split(",") if n.strip()}
    checks: list[Check] = []
    for number, runner in enumerate(runners, start=1):
        if wanted and number not in wanted:
            continue
        target = args.degradation_api or args.api if runner is check_degradation else args.api
        try:
            check = runner(target)
        except Exception as error:
            check = Check(f"{number}. {runner.__name__}", "проверка выполняется без ошибок", passed=False)
            check.note(f"ошибка выполнения: {error!r}")
        checks.append(check)
        print(f"{check.title}: {'пройдено' if check.passed else 'НЕ ПРОЙДЕНО'}")
        for line in check.lines:
            print(f"   {line}")
    if not wanted:
        args.out.write_text(render(checks, args.api), encoding="utf-8")
        print(f"отчёт: {args.out}")
    return 0 if all(c.passed for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
