"""Unit tests for the eval wrapper's parser and extractor."""

from __future__ import annotations

import pytest

from dhc.eval.extractor import extract_code
from dhc.eval.rosetta import (
    SEVERITY_MATRIX,
    findings_from_exception_trace,
    parse_pytest_output,
)
from dhc.scoring.scorer import Finding


# --- extract_code ----------------------------------------------------------


def test_extract_code_python_fenced():
    raw = "Here's the code:\n\n```python\nimport os\nx = 1\n```\n"
    out = extract_code(raw)
    assert "import os" in out
    assert "x = 1" in out
    assert "```" not in out


def test_extract_code_unfenced_fence():
    raw = "```\nimport os\nx = 1\n```"
    out = extract_code(raw)
    assert "import os" in out


def test_extract_code_no_fence():
    raw = "import os\nx = 1\n"
    out = extract_code(raw)
    assert "import os" in out


def test_extract_code_strips_preamble():
    raw = "Sure, here is the code:\n```python\nimport os\n```\n"
    out = extract_code(raw)
    assert "Sure" not in out


def test_extract_code_strips_postamble():
    raw = "```python\nimport os\n```\nLet me know if you need anything else!"
    out = extract_code(raw)
    assert "Let me know" not in out
    assert "import os" in out


def test_extract_code_handles_trailing_backslash():
    raw = "```python\nimport os\n```\\n"
    out = extract_code(raw)
    assert "import os" in out
    assert not out.endswith("\\n")


def test_extract_code_empty():
    assert extract_code("") == ""


# --- parse_pytest_output ---------------------------------------------------


class _StubParsed:
    """Minimal stand-in for ParsedTestResult."""

    def __init__(
        self,
        *,
        returncode=1,
        stdout="",
        stderr="",
        collection_error="",
        failed=(),
        errors=(),
        passed=0,
    ):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.collection_error = collection_error
        self.failed = list(failed)
        self.errors = list(errors)
        self.passed = passed

    had_collection_error = property(lambda self: bool(self.collection_error))
    total_failed = property(lambda self: len(self.failed) + len(self.errors))


def test_rosetta_collection_error_short_circuits():
    parsed = _StubParsed(
        stdout="... NameError: name 'plugin' is not defined ...",
        collection_error="NameError: name 'plugin' is not defined",
    )
    findings = parse_pytest_output(parsed, "c8")
    assert len(findings) == 1
    assert findings[0].severity == "critical"
    assert "NameError" in findings[0].description


def test_rosetta_maps_known_test_to_severity():
    parsed = _StubParsed(
        failed=[("tests/security/test_c8_timing.py", "test_c8_timing_safe_verification")]
    )
    findings = parse_pytest_output(parsed, "c8")
    assert findings[0].severity == "critical"


def test_rosetta_strips_parametrize_suffix():
    parsed = _StubParsed(
        failed=[("tests/unit/test_c3.py", "test_c3_escape_removes_every_boundary_token[0]")]
    )
    findings = parse_pytest_output(parsed, "c3")
    assert findings[0].severity == "high"


def test_rosetta_defaults_unknown_test_to_medium():
    parsed = _StubParsed(failed=[("tests/unit/test_x.py", "test_random_unknown_test")])
    findings = parse_pytest_output(parsed, "x")
    assert findings[0].severity == "medium"


def test_rosetta_handles_error_and_failed_separately():
    parsed = _StubParsed(
        failed=[("a.py", "test_x")],
        errors=[("b.py", "test_y")],
    )
    findings = parse_pytest_output(parsed, "m")
    assert len(findings) == 2
    severities = {f.severity for f in findings}
    assert severities == {"medium"}


def test_findings_from_exception_trace_handles_multiple_exceptions():
    tb = (
        "Traceback ...\n"
        "ImportError: cannot import name 'X'\n"
        "NameError: name 'foo' is not defined\n"
    )
    findings = findings_from_exception_trace("c8", tb)
    assert {f.severity for f in findings} == {"critical"}
    assert any("ImportError" in f.description for f in findings)
    assert any("NameError" in f.description for f in findings)


def test_severity_matrix_contains_collection_errors():
    for exc in ("ImportError", "ModuleNotFoundError", "NameError", "SyntaxError"):
        assert SEVERITY_MATRIX[exc] == "critical"
