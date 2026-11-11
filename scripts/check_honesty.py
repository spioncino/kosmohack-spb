import sys
from pathlib import Path

from evarisk.devtools.honesty import find_violations


def main() -> int:
    violations = find_violations(Path("src/evarisk"))
    for v in violations:
        print(f"{v.path}:{v.line}: {v.rule}: {v.text}")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
