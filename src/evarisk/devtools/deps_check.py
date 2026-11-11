import json
import re
import tomllib
from typing import Any

_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_SEPARATORS = re.compile(r"[-_.]+")
_ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*([^|]*?)\s*\|", re.MULTILINE)
_BUN_PACKAGE = re.compile(r'^\s*"([^"]+)": \["([^"]+)"', re.MULTILINE)


def normalize(name: str) -> str:
    """Имя пакета Python по PEP 503 без extras: `uvicorn[standard]` даёт `uvicorn`, `netCDF4` даёт `netcdf4`."""
    return _SEPARATORS.sub("-", name.split("[", 1)[0].strip()).lower()


def python_direct(pyproject: str, group: str | None = None) -> list[str]:
    """Прямые зависимости из текста pyproject.toml: `[project].dependencies` или группа `[dependency-groups]`."""
    data: dict[str, Any] = tomllib.loads(pyproject)
    specs: list[Any] = data["project"]["dependencies"] if group is None else data["dependency-groups"][group]
    names: set[str] = set()
    for spec in specs:
        if not isinstance(spec, str):
            continue
        match = _REQUIREMENT_NAME.match(spec)
        if match is None:
            raise ValueError(f"зависимость не разобрана: {spec!r}")
        names.add(normalize(match.group(1)))
    return sorted(names)


def uv_locked(uv_lock: str) -> dict[str, str]:
    """Версии всех пакетов из текста uv.lock по нормализованному имени."""
    data: dict[str, Any] = tomllib.loads(uv_lock)
    return {normalize(str(p["name"])): str(p["version"]) for p in data["package"]}


def npm_direct(package_json: str, dev: bool = False) -> list[str]:
    """Прямые зависимости из текста package.json: `dependencies` или `devDependencies`."""
    data: dict[str, Any] = json.loads(package_json)
    section: dict[str, str] = data.get("devDependencies" if dev else "dependencies", {})
    return sorted(section)


def bun_locked(bun_lock: str) -> dict[str, str]:
    """Версии пакетов верхнего уровня из текста bun.lock: строки `"имя": ["имя@версия", ...]`.

    Вложенные ключи вида `a/b`, у которых имя в записи не совпадает с ключом, не берутся.
    """
    found: dict[str, str] = {}
    for key, spec in _BUN_PACKAGE.findall(bun_lock):
        name, _, version = spec.rpartition("@")
        if name == key:
            found[name] = version
    return found


def documented(doc: str, heading: str) -> dict[str, str]:
    """Имя и версия из строк таблиц раздела `## heading`: имя в обратных кавычках в первой колонке, версия во второй."""
    marker = f"\n## {heading}\n"
    start = doc.find(marker)
    if start < 0:
        raise ValueError(f"нет раздела «{heading}»")
    end = doc.find("\n## ", start + len(marker))
    body = doc[start : end if end >= 0 else len(doc)]
    return dict(_ROW.findall(body))


def mismatches(rows: dict[str, str], expected: dict[str, str]) -> list[str]:
    """Расхождения строк документа с версиями lock-файла: нет строки, лишняя строка, другая версия."""
    problems: list[str] = []
    for name in sorted(rows.keys() | expected.keys()):
        if name not in rows:
            problems.append(f"{name}: нет строки, в lock-файле {expected[name]}")
        elif name not in expected:
            problems.append(f"{name}: строка есть, а прямой зависимости нет")
        elif rows[name] != expected[name]:
            problems.append(f"{name}: в документе {rows[name]}, в lock-файле {expected[name]}")
    return problems
