from pathlib import Path

from evarisk.devtools.deps_check import (
    bun_locked,
    documented,
    mismatches,
    normalize,
    npm_direct,
    python_direct,
    uv_locked,
)

ROOT = Path(__file__).resolve().parents[2]
DOC = (ROOT / "docs" / "DEPENDENCIES.md").read_text(encoding="utf-8")
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
UV_LOCK = (ROOT / "uv.lock").read_text(encoding="utf-8")
PACKAGE_JSON = (ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
BUN_LOCK = (ROOT / "frontend" / "bun.lock").read_text(encoding="utf-8")

PYTHON_RUNTIME = "Python: сервис"
PYTHON_DEV = "Python: разработка и проверки"
FRONTEND_RUNTIME = "Страница: зависимости"
FRONTEND_DEV = "Страница: сборка и тесты"

SMALL_PYPROJECT = """
[project]
dependencies = ["uvicorn[standard]>=0.30", "netCDF4>=1.7", "python-multipart", "psycopg[binary]>=3.2"]

[dependency-groups]
dev = ["pytest>=8.3", {include-group = "lint"}]
"""
SMALL_UV_LOCK = """
version = 1

[[package]]
name = "netcdf4"
version = "1.7.4"

[[package]]
name = "uvicorn"
version = "0.53.0"
"""
SMALL_BUN_LOCK = """{
  "workspaces": { "": { "dependencies": { "vue": "^3.5.0", }, }, },
  "packages": {
    "@types/node": ["@types/node@24.13.5", "", {}, "sha512-x"],
    "vue": ["vue@3.5.43", "", {}, "sha512-y"],
    "vite/esbuild": ["esbuild@0.25.0", "", {}, "sha512-z"],
  }
}
"""
SMALL_DOC = """# Документ

## Python: сервис

| пакет | версия | роль |
|---|---|---|
| `uvicorn[standard]` | 0.53.0 | сервер |
| `netCDF4` | 1.7.3 | NetCDF |

## Другое

| `numpy` | 2.5.3 | вне раздела |
"""


def _python_expected(group: str | None) -> dict[str, str]:
    locked = uv_locked(UV_LOCK)
    direct = python_direct(PYPROJECT, group)
    assert [n for n in direct if n not in locked] == []
    return {n: locked[n] for n in direct}


def _frontend_expected(dev: bool) -> dict[str, str]:
    locked = bun_locked(BUN_LOCK)
    direct = npm_direct(PACKAGE_JSON, dev)
    assert [n for n in direct if n not in locked] == []
    return {n: locked[n] for n in direct}


def test_normalize_drops_extras_and_case() -> None:
    assert normalize("uvicorn[standard]") == "uvicorn"
    assert normalize("netCDF4") == "netcdf4"
    assert normalize("pydantic_settings") == "pydantic-settings"


def test_python_direct_reads_names_without_specifiers() -> None:
    assert python_direct(SMALL_PYPROJECT) == ["netcdf4", "psycopg", "python-multipart", "uvicorn"]
    assert python_direct(SMALL_PYPROJECT, "dev") == ["pytest"]


def test_uv_locked_maps_normalized_names_to_versions() -> None:
    assert uv_locked(SMALL_UV_LOCK) == {"netcdf4": "1.7.4", "uvicorn": "0.53.0"}


def test_bun_locked_takes_top_level_packages_only() -> None:
    assert bun_locked(SMALL_BUN_LOCK) == {"@types/node": "24.13.5", "vue": "3.5.43"}


def test_documented_reads_only_its_section_and_mismatches_name_the_row() -> None:
    rows = {normalize(n): v for n, v in documented(SMALL_DOC, "Python: сервис").items()}
    assert rows == {"uvicorn": "0.53.0", "netcdf4": "1.7.3"}
    assert mismatches(rows, {"netcdf4": "1.7.4", "uvicorn": "0.53.0", "numpy": "2.5.3"}) == [
        "netcdf4: в документе 1.7.3, в lock-файле 1.7.4",
        "numpy: нет строки, в lock-файле 2.5.3",
    ]
    assert mismatches({"sgp4": "2.27"}, {}) == ["sgp4: строка есть, а прямой зависимости нет"]


def test_python_runtime_table_matches_uv_lock() -> None:
    rows = {normalize(n): v for n, v in documented(DOC, PYTHON_RUNTIME).items()}
    assert mismatches(rows, _python_expected(None)) == []


def test_python_dev_table_matches_uv_lock() -> None:
    rows = {normalize(n): v for n, v in documented(DOC, PYTHON_DEV).items()}
    assert mismatches(rows, _python_expected("dev")) == []


def test_frontend_tables_match_bun_lock() -> None:
    assert mismatches(documented(DOC, FRONTEND_RUNTIME), _frontend_expected(dev=False)) == []
    assert mismatches(documented(DOC, FRONTEND_DEV), _frontend_expected(dev=True)) == []
