from pathlib import Path

import pytest

from evarisk.config import Settings


def test_defaults_and_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EVARISK_RAW_ROOT", "/tmp/raw")
    monkeypatch.setenv("SPACETRACK_USER", "u@x.invalid")
    s = Settings()
    assert s.raw_root == Path("/tmp/raw")
    assert s.spacetrack_user == "u@x.invalid"
    assert s.spacetrack_pass is None
    assert s.http_timeout_s == 60.0
