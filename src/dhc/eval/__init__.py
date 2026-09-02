"""DHC evaluation wrapper: orchestrates LLM inference + scoring.

Submodules:
    client        OpenAI-compatible /v1/chat/completions HTTP client.
    extractor     Strip markdown fences and conversational filler.
    runner        Subprocess sandbox for pytest + stdout parser.
    backup        Reference-impl snapshot/restore.
    rosetta       Pytest-failure -> Finding severity mapping.
    prompts       Master prompt loader.
"""

from dhc.eval.client import LLMClient, LLMClientError
from dhc.eval.extractor import extract_code
from dhc.eval.runner import run_pytest_in_subprocess, ParsedTestResult
from dhc.eval.backup import ReferenceBackup
from dhc.eval.rosetta import SEVERITY_MATRIX, parse_pytest_output
from dhc.eval.prompts import PROMPTS, load_prompt

__all__ = [
    "LLMClient",
    "LLMClientError",
    "extract_code",
    "run_pytest_in_subprocess",
    "ParsedTestResult",
    "ReferenceBackup",
    "SEVERITY_MATRIX",
    "parse_pytest_output",
    "PROMPTS",
    "load_prompt",
]
