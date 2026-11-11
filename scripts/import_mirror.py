import argparse
import sys
from pathlib import Path

from evarisk.config import Settings
from evarisk.store.mirror_import import import_mirror
from evarisk.store.raw import RawStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Импорт зеркала архивов в сырое хранилище")
    parser.add_argument("--mirror", type=Path, default=Path("data/mirror"), help="каталог зеркала с manifest.jsonl")
    args = parser.parse_args()
    mirror = Path(args.mirror)
    settings = Settings()
    summary = import_mirror(mirror, RawStore(settings.raw_root))
    print(
        f"импортировано {summary.imported}, пропущено {summary.skipped}, без времени выпуска "
        f"{summary.unknown_version}, исправлена доступность у {summary.amended}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
