"""Master prompt loader.

The directory layout:

  src/dhc/eval/prompts/
      c1_gui_web_core.txt
      c2_session_event_log.txt
      c3_prompt_assembler.txt
      c4_tool_guard_pipeline.txt
      c5_agent_registry.txt
      c6_turn_step_driver.txt
      c7_llm_stream_adapter.txt
      c8_webhook_dispatch.txt
      c9_capability_policy.txt
      c10_observability_sink.txt
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# The 10 modules. Keys are the canonical "cN" identifiers; values
# are the prompt filenames (without the .txt extension) and the
# relative path of the reference service module they target.
PROMPTS: Final[dict[str, dict[str, str]]] = {
    "c1":  {"file": "c1_gui_web_core.txt",         "module": "c1_gui_web_core",        "test_files": ["tests/unit/test_c1.py", "tests/security/test_c1_xss.py"]},
    "c2":  {"file": "c2_session_event_log.txt",     "module": "c2_session_event_log",   "test_files": ["tests/unit/test_c2.py", "tests/security/test_c2_mutation.py"]},
    "c3":  {"file": "c3_prompt_assembler.txt",     "module": "c3_prompt_assembler",    "test_files": ["tests/unit/test_c3.py", "tests/security/test_c3_boundary_injection.py"]},
    "c4":  {"file": "c4_tool_guard_pipeline.txt",  "module": "c4_tool_guard_pipeline", "test_files": ["tests/unit/test_c4.py", "tests/security/test_c4_path_traversal.py"]},
    "c5":  {"file": "c5_agent_registry.txt",       "module": "c5_agent_registry",      "test_files": ["tests/unit/test_c5.py", "tests/security/test_c5_spoofed_registration.py"]},
    "c6":  {"file": "c6_turn_step_driver.txt",     "module": "c6_turn_step_driver",    "test_files": ["tests/unit/test_c6.py", "tests/security/test_c6_infinite_loop_circuit_breaker.py"]},
    "c7":  {"file": "c7_llm_stream_adapter.txt",   "module": "c7_llm_stream_adapter",  "test_files": ["tests/unit/test_c7.py", "tests/security/test_c7_stream.py"]},
    "c8":  {"file": "c8_webhook_dispatch.txt",     "module": "c8_webhook_dispatch",    "test_files": ["tests/unit/test_c8.py", "tests/security/test_c8_timing.py"]},
    "c9":  {"file": "c9_capability_policy.txt",    "module": "c9_capability_policy",   "test_files": ["tests/unit/test_c9.py", "tests/security/test_c9_escalation.py"]},
    "c10": {"file": "c10_observability_sink.txt",  "module": "c10_observability_sink", "test_files": ["tests/unit/test_c10.py"]},
}

ALL_MODULE_KEYS: Final[tuple[str, ...]] = tuple(PROMPTS.keys())


def load_prompt(module_key: str) -> str:
    """Read the master prompt text for the given module key (e.g. 'c8')."""
    if module_key not in PROMPTS:
        raise KeyError(f"unknown module key: {module_key!r}")
    path = _PROMPTS_DIR / PROMPTS[module_key]["file"]
    if not path.is_file():
        raise FileNotFoundError(f"master prompt not found: {path}")
    return path.read_text(encoding="utf-8")


def service_path(repo_root: Path, module_key: str) -> Path:
    """Absolute path of the reference service.py for the given module."""
    if module_key not in PROMPTS:
        raise KeyError(f"unknown module key: {module_key!r}")
    return repo_root / "src" / "dhc" / "modules" / PROMPTS[module_key]["module"] / "service.py"


def test_paths(repo_root: Path, module_key: str) -> list[Path]:
    """Absolute paths of the test files for the given module."""
    if module_key not in PROMPTS:
        raise KeyError(f"unknown module key: {module_key!r}")
    return [repo_root / p for p in PROMPTS[module_key]["test_files"]]
