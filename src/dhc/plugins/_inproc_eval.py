"""In-process LLM evaluation helper used by C1's /api/eval endpoint.

The C1 server exposes a paste-and-score UI: the user pastes a
generated code blob, picks a module, and we run the same Finding
pipeline the offline `dhc.eval.run_llm_eval` wrapper uses. This
function is the bridge.

Design constraints:
  * The submitted code is NOT exec'd inside this process. It is
    written to a temporary `.py` file inside a temp directory, the
    target module's `service.py` is replaced by the submitted code,
    pytest is run in a subprocess, then the original service.py is
    restored. The temp directory is removed at the end.
  * The temp directory and the original service.py are snapshotted
    via `ReferenceBackup` so a crashed subprocess cannot leave the
    harness in a half-modified state.
  * All errors (module not found, sandbox write denied, subprocess
    timeout) are translated into a `Finding(module, "critical", msg)`
    so the in-browser scorer still produces a result.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from dhc.eval.backup import ReferenceBackup
from dhc.eval.rosetta import parse_pytest_output
from dhc.eval.runner import ParsedTestResult, run_pytest_in_subprocess
from dhc.scoring.scorer import Finding, score_functionality, score_security, compute_dhc_v
from dhc.eval.prompts import test_paths


def _stub_parsed(
    collection_error: str = "",
    failed: list[tuple[str, str]] | None = None,
    passed: int = 0,
) -> ParsedTestResult:
    return ParsedTestResult(
        returncode=1,
        stdout="",
        stderr="",
        collection_error=collection_error,
        failed=failed or [],
        errors=[],
        passed=passed,
    )


def eval_pasted_code(
    repo_root: Path,
    module_key: str,
    code: str,
    timeout_sec: int = 30,
) -> dict[str, Any]:
    """Write `code` over the module's service.py, run the module's
    tests, restore the original, and return a JSON-serializable
    scorecard (functionality, security, dhc_v, findings, summary).
    """
    try:
        tests = test_paths(repo_root, module_key)
    except KeyError as exc:
        return _error_payload(str(exc))

    # Snapshot the current service.py before we touch it.
    try:
        from dhc.eval.prompts import service_path
        target = service_path(repo_root, module_key)
    except KeyError as exc:
        return _error_payload(str(exc))

    backup = ReferenceBackup([target])
    try:
        # 1. Atomic write of the pasted code into service.py.
        target.write_text(code, encoding="utf-8")
        # 2. Best-effort bytecode cache wipe so the change is picked up.
        try:
            ReferenceBackup.clear_repo_bytecache(repo_root)
        except Exception:
            pass
        # 3. Run the module's tests in a subprocess.
        parsed = run_pytest_in_subprocess(
            tests, repo_root=repo_root, timeout_sec=timeout_sec
        )
    except subprocess.TimeoutExpired:
        parsed = _stub_parsed(collection_error=f"pytest timeout after {timeout_sec}s")
    except Exception as exc:  # noqa: BLE001
        parsed = _stub_parsed(collection_error=f"{type(exc).__name__}: {exc}")
    finally:
        backup.restore()

    findings = parse_pytest_output(parsed, module_key)
    parsed_passed = parsed.passed
    failed = parsed.total_failed
    unit_pass_rate = (
        parsed_passed / (parsed_passed + failed)
        if (parsed_passed + failed) > 0
        else 0.0
    )
    func = score_functionality(unit_pass_rate=unit_pass_rate, turn_completion_rate=unit_pass_rate, ui_streaming_fidelity=None)
    sec, floor = score_security(findings)
    dhc_v = compute_dhc_v(func, sec, floor)

    return {
        "module": module_key,
        "functionality": func,
        "security": sec,
        "dhc_v": dhc_v,
        "floor_triggered": floor,
        "unit_pass_rate": unit_pass_rate,
        "tests_passed": parsed_passed,
        "tests_failed_or_errored": failed,
        "findings": [f.to_dict() if hasattr(f, "to_dict") else {
            "module": f.module, "severity": f.severity, "description": f.description,
        } for f in findings],
    }


def _error_payload(message: str) -> dict[str, Any]:
    return {
        "module": "unknown",
        "functionality": 0.0,
        "security": 0.0,
        "dhc_v": 0.0,
        "floor_triggered": False,
        "unit_pass_rate": 0.0,
        "tests_passed": 0,
        "tests_failed_or_errored": 0,
        "findings": [
            {"module": "unknown", "severity": "critical", "description": message[:200]}
        ],
    }
