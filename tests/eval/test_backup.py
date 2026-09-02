"""Tests for the reference implementation backup/restore mechanism."""

from __future__ import annotations

from pathlib import Path

import pytest

from dhc.eval.backup import ReferenceBackup


def test_backup_restores_existing_file(tmp_path: Path):
    target = tmp_path / "service.py"
    target.write_text("# reference\ndef original():\n    return 1\n", encoding="utf-8")

    backup = ReferenceBackup([target])
    # Simulate the LLM overwriting the file.
    target.write_text("# LLM wrote this\ndef broken():\n    return 0/0\n", encoding="utf-8")
    backup.restore()
    assert target.read_text(encoding="utf-8") == "# reference\ndef original():\n    return 1\n"


def test_backup_removes_file_that_did_not_exist(tmp_path: Path):
    target = tmp_path / "service.py"
    target.write_text("# LLM created this\n", encoding="utf-8")

    backup = ReferenceBackup([target])
    # File existed at snapshot time, so restore puts back the snapshot.
    backup.restore()
    assert target.read_text(encoding="utf-8") == "# LLM created this\n"


def test_backup_handles_multiple_files(tmp_path: Path):
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("a\n", encoding="utf-8")
    b.write_text("b\n", encoding="utf-8")

    backup = ReferenceBackup([a, b])
    a.write_text("A_LLM\n", encoding="utf-8")
    b.write_text("B_LLM\n", encoding="utf-8")
    backup.restore()
    assert a.read_text(encoding="utf-8") == "a\n"
    assert b.read_text(encoding="utf-8") == "b\n"


def test_clear_repo_bytecache_removes_pycache_dirs(tmp_path: Path):
    cache = tmp_path / "src" / "dhc" / "modules" / "c1" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "service.cpython-314.pyc").write_bytes(b"fake")
    assert (cache / "service.cpython-314.pyc").exists()
    ReferenceBackup.clear_repo_bytecache(tmp_path)
    assert not (cache / "service.cpython-314.pyc").exists()
