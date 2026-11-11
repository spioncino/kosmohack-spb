"""Перегенерация docs/examples: запросы берутся из result.json существующих архивов, расчёт идёт через API.

Запуск: поднять API (scripts/dev_server.sh start), затем uv run python scripts/regen_examples.py.
"""

import argparse
import io
import json
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

MODE_LABELS = {"review_2024-06-21_constraint_daylight.zip": "review + ограничение освещённости"}
TIMEOUT_SECONDS = 600


def _post(api: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    req = Request(
        api + path, data=json.dumps(body).encode(), headers={"content-type": "application/json"}, method="POST"
    )
    with urlopen(req, timeout=TIMEOUT_SECONDS) as response:
        payload: dict[str, Any] = json.loads(response.read())
    return payload


def _get(api: str, path: str) -> bytes:
    with urlopen(api + path, timeout=TIMEOUT_SECONDS) as response:
        content: bytes = response.read()
    return content


def _stamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _minutes_by_level(intervals: list[dict[str, Any]], start: str, end: str) -> str:
    lo, hi = _stamp(start), _stamp(end)
    minutes: dict[str, float] = {}
    for iv in intervals:
        a, b = max(_stamp(iv["start"]), lo), min(_stamp(iv["end"]), hi)
        if b > a:
            minutes[iv["level"]] = minutes.get(iv["level"], 0.0) + (b - a).total_seconds() / 60
    ranked = sorted(minutes.items(), key=lambda pair: pair[1], reverse=True)
    return ", ".join(f"{level} {value:.0f}" for level, value in ranked)


def _recommendation_cell(result: dict[str, Any]) -> str:
    rec = result["recommendation"]
    excluded = sum(1 for w in result["windows"] if w["excluded_reason"])
    if rec["preferred"] is None:
        text = "нет (insufficient)"
    else:
        text = f"{rec['preferred']['start'][11:16]}–{rec['preferred']['end'][11:16]}"
        if "перенос не улучшает" in rec["reason"]:
            text += " (запрошенное сохранено, D45)"
        elif "запрошенное окно хуже" in rec["reason"]:
            text += " (перенос на ближайшее чистое окно, D49)"
        elif "запрошенное окно исключено" in rec["reason"]:
            text += " (запрошенное исключено, ближайшее допустимое, D49)"
    return f"{text} (исключено окон {excluded} из {len(result['windows'])})"


def _version_cell(result: dict[str, Any]) -> str:
    text: str = result["algorithm_version"]
    v = result.get("verification")
    if v is None:
        return text
    lead = "н/о" if v["lead_time_minutes"] is None else f"{v['lead_time_minutes']:.1f} мин"
    match = "совпало" if v["window_choice_stable"] else "не совпало"
    return (
        f"{text}; проверка после события: заблаговременность {lead}, попадания {v['hit_minutes']:.0f}, "
        f"пропуски {v['miss_minutes']:.0f}, ложные {v['false_alarm_minutes']:.0f}, окно {match} с окном по факту"
    )


def _row(name: str, result: dict[str, Any]) -> str:
    req = result["request"]
    sep = result["lines"][0]
    mode = MODE_LABELS.get(name, req["mode"])
    levels = _minutes_by_level(sep["intervals"], req["search_start"], req["search_end"])
    return (
        f"| `{name}` | {mode} | {req['eva_start'][:16]}Z +{req['duration_hours']} ч | "
        f"{sep['availability']}, {sep['confidence']['grade']} | {levels} | {_recommendation_cell(result)} | "
        f"{_version_cell(result)} |"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Перегенерация архивов docs/examples через API")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--dir", type=Path, default=Path("docs/examples"))
    parser.add_argument("--full-day", action="store_true", help="продлить период поиска примеров до 24 часов")
    args = parser.parse_args()
    rows: dict[str, str] = {}
    for archive in sorted(args.dir.glob("*.zip")):
        with zipfile.ZipFile(archive) as z:
            request = json.loads(z.read("result.json"))["request"]
        if args.full_day:
            start = _stamp(request["search_start"])
            request["search_end"] = (start + timedelta(hours=24)).isoformat().replace("+00:00", "Z")
        created = _post(args.api, "/assessments", request)
        data = _get(args.api, f"/assessments/{created['id']}/export")
        archive.write_bytes(data)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            result = json.loads(z.read("result.json"))
        rows[archive.name] = _row(archive.name, result)
        print(f"{archive.name}: {_recommendation_cell(result)}")
    readme = args.dir / "README.md"
    lines = readme.read_text(encoding="utf-8").split("\n")
    for i, line in enumerate(lines):
        for name, row in rows.items():
            if line.startswith(f"| `{name}` |"):
                lines[i] = row
    readme.write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
