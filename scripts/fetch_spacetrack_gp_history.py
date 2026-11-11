#!/usr/bin/env python3
"""Выгрузка исторических орбитальных элементов МКС (NORAD 25544) из Space-Track GP_HISTORY.

Диапазон задаётся по CREATION_DATE (момент публикации набора), а не по EPOCH:
именно CREATION_DATE нужен для строгого replay. Берём запас перед 1 мая 2024,
чтобы на первую дату периода уже был опубликованный набор.

Учётные данные: переменные окружения SPACETRACK_USER / SPACETRACK_PASS
или файл .env в корне проекта. Результат: data/spacetrack/*.json + *.meta.json.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "spacetrack"
BASE = "https://www.space-track.org"
LOGIN_URL = f"{BASE}/ajaxauth/login"
NORAD_ID = 25544
CREATED_FROM = "2024-04-20"
CREATED_TO = "2024-07-02"
HISTORY_QUERY = (
    f"{BASE}/basicspacedata/query/class/gp_history/NORAD_CAT_ID/{NORAD_ID}"
    f"/CREATION_DATE/{CREATED_FROM}--{CREATED_TO}/orderby/CREATION_DATE%20asc/format/json"
)
CURRENT_QUERY = f"{BASE}/basicspacedata/query/class/gp/NORAD_CAT_ID/{NORAD_ID}/format/json"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def credentials() -> tuple[str, str]:
    load_dotenv(ROOT / ".env")
    user = os.environ.get("SPACETRACK_USER")
    password = os.environ.get("SPACETRACK_PASS")
    if not user or not password:
        sys.exit(
            "Нет учётных данных Space-Track. Скопируйте .env.example в .env и заполните "
            "SPACETRACK_USER / SPACETRACK_PASS (или задайте их как переменные окружения)."
        )
    return user, password


def login(session: requests.Session, user: str, password: str) -> None:
    resp = session.post(LOGIN_URL, data={"identity": user, "password": password}, timeout=60)
    resp.raise_for_status()
    if "Failed" in resp.text or "error" in resp.text.lower():
        sys.exit(f"Space-Track отверг вход: {resp.text.strip()[:300]}")


def fetch_json(session: requests.Session, url: str) -> list[dict[str, Any]]:
    resp = session.get(url, timeout=120)
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError:
        sys.exit(f"Ответ не JSON (возможно, истекла сессия или сработал лимит):\n{resp.text[:500]}")
    if isinstance(data, dict) and "error" in data:
        sys.exit(f"Space-Track вернул ошибку: {data}")
    return list(data)


def save(name: str, rows: list[dict[str, Any]], url: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    data_path = OUT_DIR / f"{name}.json"
    data_path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    meta = {
        "source": "Space-Track.org",
        "query_url": url,
        "fetched_at_utc": fetched_at,
        "row_count": len(rows),
        "norad_cat_id": NORAD_ID,
        "license_note": "Space-Track user agreement: данные не подлежат передаче третьим лицам без разрешения.",
    }
    if rows:
        created = sorted(r["CREATION_DATE"] for r in rows if r.get("CREATION_DATE"))
        epochs = sorted(r["EPOCH"] for r in rows if r.get("EPOCH"))
        meta.update(
            creation_date_min=created[0],
            creation_date_max=created[-1],
            epoch_min=epochs[0],
            epoch_max=epochs[-1],
        )
    (OUT_DIR / f"{name}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return data_path


def main() -> None:
    user, password = credentials()
    with requests.Session() as session:
        session.headers["User-Agent"] = "kosmohackathon-eva-risk/0.1"
        login(session, user, password)

        current = fetch_json(session, CURRENT_QUERY)
        path = save(f"gp_current_{NORAD_ID}", current, CURRENT_QUERY)
        if current:
            r = current[0]
            print(f"Текущий набор: EPOCH={r.get('EPOCH')} CREATION_DATE={r.get('CREATION_DATE')} -> {path}")

        time.sleep(2)  # лимит Space-Track: <30 запросов/мин
        history = fetch_json(session, HISTORY_QUERY)
        name = f"gp_history_{NORAD_ID}_{CREATED_FROM}_{CREATED_TO}"
        path = save(name, history, HISTORY_QUERY)
        print(f"История: {len(history)} наборов -> {path}")
        if history:
            print(f"  CREATION_DATE: {history[0]['CREATION_DATE']} .. {history[-1]['CREATION_DATE']}")
            epochs = sorted(r["EPOCH"] for r in history)
            print(f"  EPOCH:         {epochs[0]} .. {epochs[-1]}")
            by_day: dict[str, int] = {}
            for r in history:
                by_day[r["CREATION_DATE"][:10]] = by_day.get(r["CREATION_DATE"][:10], 0) + 1
            counts = sorted(by_day.values())
            print(f"  наборов в сутки: min={counts[0]} median={counts[len(counts) // 2]} max={counts[-1]}")
        else:
            print("  ВНИМАНИЕ: пустой ответ. Проверьте права учётной записи и диапазон дат.")


if __name__ == "__main__":
    main()
