"""Helper for computing the SHA-256 of a plugin's service.py for
inclusion in its manifest.json. Run from the project root:

    python -m dhc.plugins._sha <plugin_id>

Outputs the lowercase hex digest on stdout.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m dhc.plugins._sha <plugin_id>", file=sys.stderr)
        return 2
    plugin_id = sys.argv[1]
    path = Path(__file__).resolve().parent / plugin_id / "service.py"
    if not path.is_file():
        print(f"not found: {path}", file=sys.stderr)
        return 1
    print(sha256_file(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
