"""Проверка на периодах вне тестового: прогноз за сутки против факта, уровень обстановки и экспозиция экипажа отдельно.

Случаи идут через очередь заданий живого API, поэтому архив периода докачивается сам (D59). Результат:
docs/INDEPENDENT_PERIODS.md. Запуск: uv run python scripts/independent_periods.py [--api http://localhost:8000]
"""

import argparse
import json
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

POLL_S = 2.0
JOB_TIMEOUT_S = 600.0


@dataclass(frozen=True)
class Case:
    title: str
    cutoff: str
    day: str
    next_day: str
    in_test_period: bool
    used_for_hold: bool = False


CASES = (
    Case("11 мая 2024, прогноз за сутки", "2024-05-10T12:30:00Z", "2024-05-11", "2024-05-12", True),
    Case("11 мая 2024, событие идёт", "2024-05-11T03:00:00Z", "2024-05-11", "2024-05-12", True),
    Case("8 июня 2024, прогноз за сутки", "2024-06-07T12:00:00Z", "2024-06-08", "2024-06-09", True),
    Case("10 октября 2024, событие идёт", "2024-10-09T12:00:00Z", "2024-10-10", "2024-10-11", False, True),
    Case("15 марта 2025, спокойные сутки", "2025-03-14T12:00:00Z", "2025-03-15", "2025-03-16", False),
    Case("1 июня 2025, прогноз за сутки", "2025-05-31T12:00:00Z", "2025-06-01", "2025-06-02", False, True),
    Case("11 ноября 2025, перед событием", "2025-11-10T12:00:00Z", "2025-11-11", "2025-11-12", False, True),
    Case("12 ноября 2025, событие идёт", "2025-11-11T12:00:00Z", "2025-11-12", "2025-11-13", False, True),
)


def call(api: str, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(api + path, method=method, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)


def run_case(api: str, case: Case) -> dict[str, Any]:
    body = {
        "mode": "replay",
        "cutoff": case.cutoff,
        "eva_start": f"{case.day}T04:00:00Z",
        "duration_hours": 6,
        "search_start": f"{case.day}T00:00:00Z",
        "search_end": f"{case.next_day}T00:00:00Z",
    }
    job_id = call(api, "POST", "/jobs", body)["job_id"]
    started = time.monotonic()
    while True:
        job = call(api, "GET", f"/jobs/{job_id}")
        if job["status"] in ("done", "failed"):
            break
        if time.monotonic() - started > JOB_TIMEOUT_S:
            raise RuntimeError(f"задание {job_id} не завершилось за {JOB_TIMEOUT_S:g} с")
        time.sleep(POLL_S)
    if job["status"] == "failed":
        raise RuntimeError(f"{case.title}: {job['error']}")
    result: dict[str, Any] = call(api, "GET", f"/assessments/{job['assessment_id']}")
    return result


def window_text(window: dict[str, str] | None) -> str:
    return "нет" if window is None else f"{window['start'][11:16]}–{window['end'][11:16]}"


def row(case: Case, result: dict[str, Any]) -> tuple[str, dict[str, float]]:
    v = result["verification"]
    exposure = {k: float(v[f"exposure_{k}_minutes"]) for k in ("hit", "miss", "false_alarm")}
    period = "тестовый" if case.in_test_period else "вне тестового"
    text = (
        f"| {case.title} | {period} | {'да' if case.used_for_hold else 'нет'} | "
        f"{case.cutoff[:16].replace('T', ' ')} | {window_text(result['recommendation']['preferred'])} | "
        f"{exposure['hit']:.0f} / {exposure['miss']:.0f} / {exposure['false_alarm']:.0f} | "
        f"{v['unverified_minutes']:.0f} | {'да' if v['window_choice_stable'] else 'нет'} |"
    )
    return text, exposure


def total_text(title: str, total: dict[str, float]) -> str:
    return (
        f"{title}: попаданий {total['hit']:.0f} мин, пропусков {total['miss']:.0f} мин, ложных тревог "
        f"{total['false_alarm']:.0f} мин."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Прогноз за сутки против факта на периодах вне тестового")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--out", type=Path, default=Path("docs/INDEPENDENT_PERIODS.md"))
    args = parser.parse_args()
    rows: list[str] = []
    total = {"hit": 0.0, "miss": 0.0, "false_alarm": 0.0}
    clean = {"hit": 0.0, "miss": 0.0, "false_alarm": 0.0}
    version = ""
    for case in CASES:
        result = run_case(args.api, case)
        version = result["algorithm_version"]
        text, exposure = row(case, result)
        rows.append(text)
        for key, value in exposure.items():
            total[key] += value
            if not case.used_for_hold:
                clean[key] += value
        print(text)
    lines = [
        "# Прогноз за сутки против факта: тестовый период и периоды вне его",
        "",
        f"Прогон {datetime.now(UTC):%Y-%m-%d %H:%M} UTC, версия алгоритма `{version}`, "
        "`scripts/independent_periods.py`. "
        "Запрошенное окно 04:00 + 6 ч, поиск по суткам, режим «прогноз из прошлого»: расчёт видит только записи, "
        "доступные на момент отсечения. Архив периодов вне мая–июня 2024 докачан заданием расчёта (D59).",
        "",
        "Что считается. Уровни high и critical линии протонов означают экспозицию экипажа: минуты геомагнитного "
        "доступа, на которых протоны достают станцию (D63). Попадание это минута, названная и прогнозом, и фактом; "
        "пропуск назван только фактом; ложная тревога названа только прогнозом. Факт это тот же расчёт по всем "
        "опубликованным позже данным: сверка проверяет своевременность данных и правила, а физику обрезания "
        "проверяет литература (D62), поэтому точка отсчёта широты обрезания стоит первой в списке на сверку.",
        "",
        "Честная оговорка о независимости. Сроки удержания события D60 (48 ч и 24 ч) взяты из длительностей событий "
        "9 октября 2024, 31 мая 2025 и 10–13 ноября 2025, поэтому строки этих событий отмечены в колонке «срок D60 "
        "снят с этого события» и посчитаны отдельно. Правило D63 (уровни high и critical только на витках доступа) "
        "принято после разбора 10 октября 2024. Восемь строк это шесть эпизодов: 11 мая 2024 и ноябрь 2025 года "
        "посчитаны по два отсечения. Поэтому итог ниже это не общая точность модели, а проверка правил на этих "
        "эпизодах; числовые параметры моделей на них не подбирались.",
        "",
        "| случай | период | срок D60 снят с этого события | отсечение UTC | рекомендованное окно | "
        "экспозиция: попадания / пропуски / ложные, мин | минут без факта | окно устояло по факту |",
        "|---|---|---|---|---|---|---|---|",
        *rows,
        "",
        total_text("Итог по всем строкам", total),
        "",
        total_text("Итог по строкам, не участвовавшим в выборе срока D60", clean),
        "",
        "Как читать. 8 июня 2024 и 1 июня 2025 событие началось после отсечения, SWPC давал вероятность 10 %: "
        "вспышку за сутки не предсказывает никто, эти минуты пропущены, и сервис прямо говорит в рекомендации, что "
        "поток в периоде не измерен. 10 октября 2024 и 12 ноября 2025 событие шло на момент отсечения: правило D60 "
        "удержало его на следующие сутки. 11 ноября 2025 удержание дало ложные тревоги: событие по факту ослабло, "
        "а сообщения об окончании DONKI не выпускал; ложное продление выбрано сознательно как более безопасная "
        "ошибка. Ложные тревоги в остальных строках это витки, открытые в прогнозе при более высоком Kp, чем "
        "оказался по факту. 11 и 12 ноября 2025 окна нет ни в прогнозе, ни по факту: в официальном файле почасового "
        "прогноза NASA MEO на 2025 год (NTRS 20240012381) нет часов с 7 ноября 20:00 по 20 ноября 21:00 UTC, линия "
        "метеоров на эти сутки без уровня, и окна исключены как окна с пробелом данных (D70); счёты экспозиции по "
        "протонам от этого не зависят.",
        "",
    ]
    args.out.write_text("\n".join(lines), encoding="utf-8")
    print(f"отчёт: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
