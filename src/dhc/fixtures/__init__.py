"""Package shim: re-exports the `fixtures` tree at the repo root as
`dhc.fixtures.*` so test modules can import it under the package name
without moving the fixture files under `src/`.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
_FIXTURES_ROOT = _REPO_ROOT / "fixtures"

# Load the fixture submodules under the dhc.fixtures.* namespace so
# `from dhc.fixtures.mock_llm.scripts import ...` resolves to
# `fixtures/mock_llm/scripts.py` on disk.
for sub in ("mock_llm",):
    sub_path = _FIXTURES_ROOT / sub
    if not sub_path.is_dir():
        continue
    init_file = sub_path / "__init__.py"
    if not init_file.exists():
        init_file.write_text("", encoding="utf-8")
    full_name = f"dhc.fixtures.{sub}"
    spec = importlib.util.spec_from_file_location(
        full_name,
        init_file,
        submodule_search_locations=[str(sub_path)],
    )
    if spec is None or spec.loader is None:
        continue
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)

# Also re-export the top-level `fixtures` package under `dhc.fixtures`
# so `import dhc.fixtures.mock_llm.scripts` works.
_root_init = _FIXTURES_ROOT / "__init__.py"
if not _root_init.exists():
    _root_init.write_text("", encoding="utf-8")
spec = importlib.util.spec_from_file_location(
    "dhc.fixtures",
    _root_init,
    submodule_search_locations=[str(_FIXTURES_ROOT)],
)
if spec is not None and spec.loader is not None:
    module = importlib.util.module_from_spec(spec)
    sys.modules["dhc.fixtures"] = module
    spec.loader.exec_module(module)
