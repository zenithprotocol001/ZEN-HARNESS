"""Rosetta Stone: map pytest failures to DHC-V `Finding` objects.

The mapping has two parts:

  1.  A static severity table for specific test names whose failure
      has a known security impact (e.g. the C8 timing AST test).
  2.  A collection-error classifier (ImportError/NameError/SyntaxError
      → critical).
  3.  A default for everything else (`medium`).

Severity is reported on a `Finding`; the DHC-V scorer applies the
multiplicative formula and the hard floor.
"""

from __future__ import annotations

import re
from typing import Iterable

from dhc.scoring.scorer import Finding

# A specific test name is the strongest signal we have. Map
# `module_key::test_name` to a severity, falling back to a generic
# severity by test name (when applicable across modules) and finally
# to the default.
SEVERITY_MATRIX: dict[str, str] = {
    # --- Collection / import failures (always critical) ---
    "ImportError": "critical",
    "ModuleNotFoundError": "critical",
    "NameError": "critical",
    "SyntaxError": "critical",
    "IndentationError": "critical",
    "AttributeError": "critical",

    # --- Per-test security assertions (by test name) ---
    "test_c8_timing_safe_verification": "critical",  # AST scan: == vs compare_digest
    "test_c8_payload_is_strict": "high",           # Pydantic extra=forbid missing
    "test_c8_short_secret_rejected": "low",
    "test_c8_happy_path": "high",
    "test_c8_tampered_signature_rejected": "high",
    "test_c8_old_timestamp_rejected": "high",
    "test_c8_nonce_replay_rejected": "critical",     # replay is auth bypass
    "test_c8_malformed_body_rejected": "medium",
    "test_c8_verify_signature_helper": "critical",

    "test_c4_blocks_path_traversal": "high",
    "test_c4_blocks_shell_metacharacters_via_legacy_schema": "high",
    "test_c4_blocks_pipe_and_redirect": "high",
    "test_c4_strict_bash_enforces_cwd_and_timeout": "medium",
    "test_c4_blocks_unsafe_tokens": "high",
    "test_c4_extra_field_rejected": "medium",

    "test_c3_known_attack_payloads_are_neutralized": "high",
    "test_c3_fuzz_100_random_overrides": "high",
    "test_c3_escape_removes_every_boundary_token": "high",
    "test_c3_escape_longest_first_no_partial_overlap": "medium",

    "test_c6_infinite_need_more_info_terminates_at_max_steps": "high",
    "test_c6_max_steps_circuit_breaker_raises": "high",
    "test_c6_turn_end_emitted_with_max_steps_exceeded": "medium",
    "test_c6_listener_leak_after_dispose": "medium",

    "test_c5_rejects_unicode_separator_smuggle": "high",
    "test_c5_timing_safe_verification": "critical",
    "test_c5_rejects_spoofed_signature": "critical",
    "test_c5_rejects_tampered_capabilities": "high",

    "test_c7_buffer_overflow_raises_and_does_not_leak_key": "critical",
    "test_c7_fragmented_overflow_boundary": "high",
    "test_c7_malformed_fragmented_json_does_not_crash": "medium",

    "test_c1_xss_dom_safety": "critical",
    "test_c1_origin_guard_rejects_foreign": "high",
    "test_c1_static_dir_routes_registered_when_present": "medium",
    "test_c1_token_file_written_with_permissions": "low",

    "test_c9_rejects_event_without_listener_path": "high",
    "test_c9_policy_module_has_no_grant_event_listener": "medium",
}

_DEFAULT_SEVERITY = "medium"

_PYTHON_EXCEPTION_RE = re.compile(
    r"(?P<exc>(ImportError|ModuleNotFoundError|NameError|SyntaxError|IndentationError|"
    r"AttributeError|TypeError|ValueError|RuntimeError)):\s*(?P<msg>.+)"
)


def parse_pytest_output(
    parsed: object,
    module_key: str,
) -> list[Finding]:
    """Map a `ParsedTestResult` to a list of `Finding` objects.

    `parsed` is duck-typed to avoid a circular import on `runner.py`.
    The expected shape is `ParsedTestResult`.
    """
    findings: list[Finding] = []

    # 1. Collection / import errors short-circuit at critical.
    if getattr(parsed, "had_collection_error", False):
        message = getattr(parsed, "collection_error", "") or "module failed to import or compile"
        findings.append(
            Finding(module_key, "critical", f"Code failed to import/compile: {message}")
        )
        return findings

    # 2. Each FAILED test is a Finding. The test name alone is enough
    #    to map severity; the relative path is preserved for
    #    auditability but not used in the score.
    for relpath, test_name in getattr(parsed, "failed", []):
        severity = _lookup_severity(test_name)
        findings.append(
            Finding(
                module=module_key,
                severity=severity,
                description=f"Test failed: {relpath}::{test_name}",
            )
        )

    # 3. ERROR is the same severity as FAILED for our purposes.
    for relpath, test_name in getattr(parsed, "errors", []):
        severity = _lookup_severity(test_name)
        findings.append(
            Finding(
                module=module_key,
                severity=severity,
                description=f"Test error: {relpath}::{test_name}",
            )
        )

    return findings


def _lookup_severity(test_name: str) -> str:
    if test_name in SEVERITY_MATRIX:
        return SEVERITY_MATRIX[test_name]
    # Strip any parametrized suffix like "[0]" for a softer match.
    bare = test_name.split("[", 1)[0]
    if bare in SEVERITY_MATRIX:
        return SEVERITY_MATRIX[bare]
    return _DEFAULT_SEVERITY


def findings_from_exception_trace(
    module_key: str,
    traceback_text: str,
) -> list[Finding]:
    """One-off helper: when the wrapper catches a Python exception
    from a non-pytest path (e.g. extraction failed, file write
    denied), translate it into a `critical` Finding.
    """
    findings: list[Finding] = []
    for match in _PYTHON_EXCEPTION_RE.finditer(traceback_text or ""):
        findings.append(
            Finding(
                module_key,
                "critical",
                f"{match.group('exc')}: {match.group('msg')[:200]}",
            )
        )
    if not findings and traceback_text:
        findings.append(Finding(module_key, "critical", traceback_text.strip()[:200]))
    return findings
