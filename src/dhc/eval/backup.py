"""Reference implementation backup and restore.

The wrapper overwrites each module's `service.py` with the LLM's
generated code before running the test suite, and must restore the
pristine reference implementation before the next module's run.

We snapshot the file bytes at startup and write them back on
restore. We also clear the Python bytecode cache so a subsequent
pytest run imports the restored file, not the LLM-generated one.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Iterable


class ReferenceBackup:
    """Snapshot a set of files and restore them on demand.

    Usage:
        backup = ReferenceBackup([path1, path2, ...])
        try:
            ... run LLM-generated tests ...
        finally:
            backup.restore()
    """

    def __init__(self, paths: Iterable[Path]) -> None:
        self._paths = [Path(p) for p in paths]
        self._snapshots: dict[Path, bytes] = {}
        self._existed: dict[Path, bool] = {}
        self._snapshot()

    def _snapshot(self) -> None:
        for p in self._paths:
            if p.is_file():
                self._snapshots[p] = p.read_bytes()
                self._existed[p] = True
            else:
                self._snapshots[p] = b""
                self._existed[p] = False

    def restore(self) -> None:
        for p in self._paths:
            if self._existed.get(p, False):
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(self._snapshots[p])
            else:
                if p.is_file() or p.is_symlink():
                    p.unlink()
            self._clear_bytecode(p)

    @staticmethod
    def _clear_bytecode(p: Path) -> None:
        """Remove `__pycache__/*.pyc` entries for the module so the next
        pytest run imports the restored source file rather than a
        stale compiled artifact."""
        cache = p.parent / "__pycache__"
        if not cache.is_dir():
            return
        stem = p.stem
        for c in cache.glob(f"{stem}.*.pyc"):
            try:
                c.unlink()
            except OSError:
                pass

    @staticmethod
    def clear_repo_bytecache(repo_root: Path) -> None:
        """Best-effort wipe of all `__pycache__` under the project root."""
        for cache in repo_root.rglob("__pycache__"):
            if not cache.is_dir():
                continue
            for c in cache.glob("*.pyc"):
                try:
                    c.unlink()
                except OSError:
                    pass
            try:
                shutil.rmtree(cache, ignore_errors=True)
            except OSError:
                pass
