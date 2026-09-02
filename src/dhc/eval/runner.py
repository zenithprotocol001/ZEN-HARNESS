"""Sandboxed pytest execution and stdout parser.

The subprocess invocation gives us:
  * Crash isolation: a rogue LLM script that segfaults Python (or
    hangs) cannot kill the wrapper.
  * A hard wall-clock cap (`timeout_sec`) prevents the wrapper from
    itself becoming a DoS vector.
  * A clean stdout/stderr pair to feed the parser.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass
class ParsedTestResult:
    """Structured view of one pytest invocation."""

    returncode: int
    stdout: str
    stderr: str
    collection_error: str = ""
    # List of (test_path_rel, test_name) for each FAILED line.
    failed: list[tuple[str, str]] = field(default_factory=list)
    # List of (test_path_rel, test_name) for each ERROR line.
    errors: list[tuple[str, str]] = field(default_factory=list)
    # List of (test_path_rel, test_name) for each SKIPPED line.
    skipped: list[tuple[str, str]] = field(default_factory=list)
    # Number of "passed" lines (if pytest ran verbosely).
    passed: int = 0
    # Last "short test summary info" block, verbatim.
    summary: str = ""

    @property
    def had_collection_error(self) -> bool:
        return bool(self.collection_error)

    @property
    def total_failed(self) -> int:
        return len(self.failed) + len(self.errors)

    @property
    def total_skipped(self) -> int:
        return len(self.skipped)


_FAILED_LINE = re.compile(r"^FAILED\s+(?P<path>\S+)::(?P<name>\S+)", re.MULTILINE)
_ERROR_LINE = re.compile(r"^ERROR\s+(?P<path>\S+)::(?P<name>\S+)", re.MULTILINE)
_SKIPPED_LINE = re.compile(r"^SKIPPED\s+(?P<path>\S+)::(?P<name>\S+)", re.MULTILINE)
_PASSED_LINE = re.compile(r"^PASSED\s+(?P<path>\S+)", re.MULTILINE)
_COLLECTION_ERROR = re.compile(
    r"(?P<header>={3,}\s*ERRORS?\s*={3,}|"
    r"ERROR\s+collecting\s+|"
    r"\.{3,}\s*ERROR\s*collecting\s*)",
    re.IGNORECASE,
)
_SUMMARY_BLOCK = re.compile(
    r"(?P<block>={3,}\s*short test summary info\s*={3,}.*?={3,}\s*\d+\s+passed.*?\Z)",
    re.DOTALL,
)


def run_pytest_in_subprocess(
    test_files: Iterable[Path],
    *,
    repo_root: Path,
    timeout_sec: int = 60,
    extra_args: list[str] | None = None,
    python_executable: str | None = None,
) -> ParsedTestResult:
    """Run pytest on the given test files in a subprocess and return
    the parsed result."""
    py = python_executable or sys.executable
    args: list[str] = [
        py,
        "-m",
        "pytest",
        *[str(p.relative_to(repo_root)) for p in test_files],
        "-v",
        "--tb=short",
        "--no-header",
    ]
    if extra_args:
        args.extend(extra_args)
    # PYTHONPATH so the in-process pytest can import `src.dhc.*`.
    import os
    env = dict(os.environ)
    src = repo_root / "src"
    env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        proc = subprocess.run(
            args,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=env,
        )
        stdout, stderr, returncode = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        return ParsedTestResult(
            returncode=-1,
            stdout=exc.stdout or "",
            stderr=(exc.stderr or "") + f"\n[wrapper] timeout after {timeout_sec}s",
            collection_error="subprocess timeout",
        )

    result = ParsedTestResult(returncode=returncode, stdout=stdout, stderr=stderr)
    _populate_collection_error(result)
    _populate_outcome_lines(result)
    _populate_summary(result)
    return result


def _populate_collection_error(result: ParsedTestResult) -> None:
    if not result.stdout and not result.stderr:
        return
    haystack = result.stdout + "\n" + result.stderr
    if _COLLECTION_ERROR.search(haystack):
        # Try to capture the actual ImportError / NameError line.
        for line in haystack.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if any(
                keyword in stripped
                for keyword in (
                    "ImportError:",
                    "ModuleNotFoundError:",
                    "NameError:",
                    "SyntaxError:",
                    "IndentationError:",
                    "AttributeError:",
                )
            ):
                result.collection_error = stripped
                return
        result.collection_error = "collection error (cause not parsed)"


def _populate_outcome_lines(result: ParsedTestResult) -> None:
    for match in _FAILED_LINE.finditer(result.stdout):
        result.failed.append((match.group("path"), match.group("name")))
    for match in _ERROR_LINE.finditer(result.stdout):
        result.errors.append((match.group("path"), match.group("name")))
    for match in _SKIPPED_LINE.finditer(result.stdout):
        result.skipped.append((match.group("path"), match.group("name")))
    for _ in _PASSED_LINE.finditer(result.stdout):
        result.passed += 1


def _populate_summary(result: ParsedTestResult) -> None:
    match = _SUMMARY_BLOCK.search(result.stdout)
    if match:
        result.summary = match.group("block").strip()
