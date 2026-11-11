#!/usr/bin/env python3
"""Зеркало открытых архивов за заданный период в data/mirror с манифестом на каждый файл.

Логика лежит в `evarisk.sources.archive_mirror` и общая с докачкой архива по запросу (D59).
Модуль зеркала использует только стандартную библиотеку, поэтому скрипт запускается до установки зависимостей.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evarisk.sources.archive_mirror import Mirror, mirror_period  # noqa: E402

MIRROR = ROOT / "data" / "mirror"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=date.fromisoformat, default=date(2024, 3, 1))
    p.add_argument("--end", type=date.fromisoformat, default=date(2024, 7, 2))
    a = p.parse_args()
    mir = Mirror(MIRROR, report=lambda text: print(text, flush=True))
    mirror_period(mir, a.start, a.end)
    print(f"готово: новых {mir.new}, пропущено {mir.skipped}, ошибок {len(mir.failed)}")
    for f in mir.failed:
        print("  ", f)
    return 1 if mir.failed else 0


if __name__ == "__main__":
    sys.exit(main())
