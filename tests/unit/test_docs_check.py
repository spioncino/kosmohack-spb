from pathlib import Path

from evarisk.devtools.docs_check import missing_module_readme, module_of, modules_without_status


def test_module_of() -> None:
    assert module_of("src/evarisk/store/raw.py") == "store"
    assert module_of("src/evarisk/types.py") == "core"
    assert module_of("tests/unit/test_x.py") is None


def test_missing_module_readme(tmp_path: Path) -> None:
    (tmp_path / "store").mkdir()
    (tmp_path / "store" / "__init__.py").write_text("")
    (tmp_path / "store" / "raw.py").write_text("")
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "__init__.py").write_text("")
    (tmp_path / "sources" / "README.md").write_text("# sources")
    assert missing_module_readme(tmp_path) == [tmp_path / "store"]


def test_modules_without_status() -> None:
    status = "| core | готово | 2026-09-18 | | |\n| store | в работе | 2026-09-18 | | |\n"
    changed = ["src/evarisk/store/raw.py", "src/evarisk/orbit/oem.py", "docs/STATUS.md"]
    assert modules_without_status(changed, status) == ["orbit"]
