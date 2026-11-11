from pathlib import Path

from evarisk.devtools.honesty import find_violations


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_detects_fill_zero_in_risk(tmp_path: Path) -> None:
    write(tmp_path, "risk/x.py", "df = df.fillna(0)\n")
    rules = {v.rule for v in find_violations(tmp_path)}
    assert "fillna" in rules


def test_detects_silent_except_and_naive_now(tmp_path: Path) -> None:
    write(tmp_path, "windows/x.py", "try:\n    pass\nexcept Exception:\n    pass\nt = datetime.now()\n")
    rules = {v.rule for v in find_violations(tmp_path)}
    assert {"silent_except", "naive_now"} <= rules


def test_detects_reader_addressed_comment_anywhere(tmp_path: Path) -> None:
    write(tmp_path, "api/x.py", "x = 1  # note that you should check this\n")
    write(tmp_path, "store/y.py", "y = 2  # обратите внимание\n")
    paths = {v.path.name for v in find_violations(tmp_path) if v.rule == "comment_address"}
    assert paths == {"x.py", "y.py"}


def test_allows_fillna_outside_calc_modules(tmp_path: Path) -> None:
    write(tmp_path, "parsers/x.py", "s = s.fillna(0)\n")
    assert [v for v in find_violations(tmp_path) if v.rule == "fillna"] == []


def test_detects_direct_table_import_in_calc(tmp_path: Path) -> None:
    write(tmp_path, "replay/x.py", "from evarisk.store.models import KpValue\n")
    assert any(v.rule == "bypass_asof" for v in find_violations(tmp_path))
