import re
from pathlib import Path

SRC_PREFIX = "src/evarisk/"


def module_of(path: str) -> str | None:
    if not path.startswith(SRC_PREFIX) or not path.endswith(".py"):
        return None
    parts = path[len(SRC_PREFIX) :].split("/")
    return parts[0] if len(parts) > 1 else "core"


def missing_module_readme(src_root: Path) -> list[Path]:
    result: list[Path] = []
    for d in sorted(p for p in src_root.iterdir() if p.is_dir() and (p / "__init__.py").exists()):
        if d.name.startswith("_") or d.name == "devtools":
            continue
        if not (d / "README.md").exists():
            result.append(d)
    return result


def modules_without_status(changed: list[str], status_text: str) -> list[str]:
    touched = sorted({m for m in (module_of(p) for p in changed) if m})
    if not touched:
        return []
    if "docs/STATUS.md" not in changed:
        return touched
    listed = set(re.findall(r"^\|\s*([a-z_]+)\s*\|", status_text, flags=re.MULTILINE))
    return [m for m in touched if m not in listed]
