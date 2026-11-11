import re
import shutil
import subprocess
from pathlib import Path

from evarisk.version import CONFIG_DIR, GIT_SHA_ENV, algorithm_version, clean_git_env, config_hash, git_sha


def _git(repo: Path, *args: str) -> str:
    cmd = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.org", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=True, env=clean_git_env()).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "docs" / "n.md").write_text("n\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_git_sha_is_short_head_of_repo(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert git_sha(repo, env={}) == _git(repo, "rev-parse", "--short", "HEAD")


def test_changed_code_is_marked_dirty_but_changed_docs_are_not(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head = _git(repo, "rev-parse", "--short", "HEAD")
    (repo / "docs" / "n.md").write_text("changed\n", encoding="utf-8")
    assert git_sha(repo, env={}) == head
    (repo / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert git_sha(repo, env={}) == f"{head}-dirty"


def test_untracked_code_file_is_dirty(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "src" / "b.py").write_text("y = 1\n", encoding="utf-8")
    assert git_sha(repo, env={}).endswith("-dirty")


def test_environment_sha_has_priority_over_git(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert git_sha(repo, env={GIT_SHA_ENV: "abc1234"}) == "abc1234"


def test_directory_without_repository_gives_nogit(tmp_path: Path) -> None:
    assert git_sha(tmp_path, env={}) == "nogit"


def test_algorithm_version_is_process_sha_plus_config_hash() -> None:
    assert re.fullmatch(r"[0-9a-f]{7,}(-dirty)?\+cfg[0-9a-f]{8}|nogit\+cfg[0-9a-f]{8}", algorithm_version())


def test_child_git_ignores_hook_environment(tmp_path: Path, monkeypatch: object) -> None:
    # Внутри pre-commit из рабочего дерева git задаёт GIT_DIR и GIT_INDEX_FILE; без очистки окружения `git init`
    # теста переинициализировал настоящий репозиторий (core.bare = true) и переписывал его индекс.
    import pytest

    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    decoy = tmp_path / "decoy.git"
    monkeypatch.setenv("GIT_DIR", str(decoy))
    monkeypatch.setenv("GIT_INDEX_FILE", str(decoy / "index"))
    assert "GIT_DIR" not in clean_git_env() and "GIT_INDEX_FILE" not in clean_git_env()
    work = tmp_path / "work"
    work.mkdir()
    repo = _repo(work)
    assert git_sha(repo, env={}) == _git(repo, "rev-parse", "--short", "HEAD")
    assert not decoy.exists()


# Дерево конфигурации с теми же видами файлов, что в config/: правила TOML, таблица CSV, подкаталог и файл не в UTF-8
# (байт 0xFF в UTF-8 не встречается).
TREE: dict[str, bytes] = {
    "windows.toml": b"step_minutes = 15\nweight_high = 1.0\n",
    "sep_line.toml": "suit_threshold_mev = 30.0\n# порог скафандра\n".encode(),
    "meteors_2024.toml": b'[[shower]]\nname = "Perseids"\nzhr = 100\n',
    "pstar_aluminum.csv": b"energy_mev,csda_g_cm2\n10,0.17\n",
    "tables/extra.toml": b"a = 1\n",
    "table.bin": b"\xff\x00\r\n\x01",
}


def _write(root: Path, files: dict[str, bytes]) -> Path:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return root


def _crlf(content: bytes) -> bytes:
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return content
    return content.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def test_crlf_checkout_of_the_same_tree_gives_the_same_hash(tmp_path: Path) -> None:
    lf = _write(tmp_path / "lf", TREE)
    crlf = _write(tmp_path / "crlf", {name: _crlf(content) for name, content in TREE.items()})
    assert (crlf / "windows.toml").read_bytes() == b"step_minutes = 15\r\nweight_high = 1.0\r\n"
    assert config_hash(lf) == config_hash(crlf)


def test_crlf_copy_of_the_real_config_gives_the_same_hash(tmp_path: Path) -> None:
    # Checkout с core.autocrlf=true дописывает CR перед каждым LF текстовых файлов; хеш от этого не зависит.
    lf = tmp_path / "lf"
    shutil.copytree(CONFIG_DIR, lf)
    crlf = tmp_path / "crlf"
    shutil.copytree(CONFIG_DIR, crlf)
    changed = 0
    for path in sorted(p for p in crlf.rglob("*") if p.is_file()):
        content = path.read_bytes()
        path.write_bytes(_crlf(content))
        changed += path.read_bytes() != content
    assert changed > 0
    assert config_hash(lf) == config_hash(crlf)


def test_file_not_in_utf8_is_hashed_byte_for_byte(tmp_path: Path) -> None:
    before = config_hash(_write(tmp_path / "a", TREE))
    after = config_hash(_write(tmp_path / "b", {**TREE, "table.bin": b"\xff\x00\n\x01"}))
    assert before != after


def test_meteor_config_change_changes_the_hash(tmp_path: Path) -> None:
    # Прежний хеш читал только sep_line.toml и windows.toml: правка потоков метеоров версию не меняла.
    before = config_hash(_write(tmp_path / "a", TREE))
    meteors = TREE["meteors_2024.toml"].replace(b"zhr = 100", b"zhr = 120")
    assert config_hash(_write(tmp_path / "b", {**TREE, "meteors_2024.toml": meteors})) != before


def test_change_of_any_real_config_file_changes_the_hash(tmp_path: Path) -> None:
    root = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, root)
    base = config_hash(root)
    files = sorted(p for p in root.rglob("*") if p.is_file())
    assert len(files) > 2
    for path in files:
        content = path.read_bytes()
        path.write_bytes(content + b"1")
        assert config_hash(root) != base, path.name
        path.write_bytes(content)
    assert config_hash(root) == base


def test_added_or_renamed_file_changes_the_hash(tmp_path: Path) -> None:
    base = config_hash(_write(tmp_path / "a", TREE))
    assert config_hash(_write(tmp_path / "b", {**TREE, "conjunctions.toml": b"margin_minutes = 30\n"})) != base
    assert config_hash(_write(tmp_path / "c", {**TREE, "tables/new.toml": b""})) != base
    renamed = {("meteors_2025.toml" if k == "meteors_2024.toml" else k): v for k, v in TREE.items()}
    assert config_hash(_write(tmp_path / "d", renamed)) != base


def test_boundary_between_path_and_content_is_part_of_the_hash(tmp_path: Path) -> None:
    # Путь и содержимое входят в хеш со своими длинами: склейка «ab.toml» + «c» и «a» + «b.tomlc» одна и та же,
    # а наборы файлов разные.
    one = config_hash(_write(tmp_path / "a", {"ab.toml": b"c"}))
    two = config_hash(_write(tmp_path / "b", {"a": b"b.tomlc"}))
    assert one != two
