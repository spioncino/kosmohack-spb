import subprocess
import sys
from pathlib import Path

from evarisk.devtools.docs_check import missing_module_readme, modules_without_status


def changed_files(staged: bool) -> list[str]:
    args = ["git", "diff", "--name-only", "--cached"] if staged else ["git", "diff", "--name-only", "HEAD"]
    out = subprocess.run(args, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def main() -> int:
    staged = "--staged" in sys.argv
    problems: list[str] = []
    for d in missing_module_readme(Path("src/evarisk")):
        problems.append(f"нет README.md в модуле {d}")
    status = Path("docs/STATUS.md").read_text(encoding="utf-8") if Path("docs/STATUS.md").exists() else ""
    changed = changed_files(staged)
    for m in modules_without_status(changed, status):
        problems.append(f"модуль {m} изменён, но строка {m} в docs/STATUS.md не обновлена в этом коммите")
    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
