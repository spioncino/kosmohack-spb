import sys

from evarisk.config import Settings
from evarisk.worker.loop import run_forever


def main() -> int:
    run_forever(Settings())
    return 0


if __name__ == "__main__":
    sys.exit(main())
