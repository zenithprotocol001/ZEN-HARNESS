"""Strip markdown code fences and conversational filler from an LLM
response, returning the raw Python code.

The strategy is best-effort and safe-by-default:
  1.  Look for a ```` ```python ... ``` ```` block (the dominant case).
  2.  Fall back to a generic ```` ``` ... ``` ```` block.
  3.  As a last resort, return the trimmed response.
"""

from __future__ import annotations

import re

_PY_FENCE = re.compile(r"```python\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_ANY_FENCE = re.compile(r"```\s*(.*?)\s*```", re.DOTALL)
_FENCE_OPEN = re.compile(r"^```([a-zA-Z0-9_+\-]*)\s*$", re.MULTILINE)
# Strip common conversational preambles that some models emit even
# inside a code block (e.g. "Sure! Here's the code:\n```python\n...").
_PREAMBLE_PREFIXES = (
    "sure,",
    "sure.",
    "of course,",
    "of course.",
    "here is",
    "here's",
    "below is",
    "below you'll find",
    "this is the implementation",
)


def extract_code(raw_response: str) -> str:
    """Return the raw Python code from an LLM response, stripping any
    markdown code fences and conversational preambles."""
    if not raw_response:
        return ""

    text = raw_response.strip()

    # 1. ```python ... ```
    match = _PY_FENCE.search(text)
    if match:
        return _clean_fence_body(match.group(1))

    # 2. ``` ... ```
    match = _ANY_FENCE.search(text)
    if match:
        return _clean_fence_body(match.group(1))

    # 3. Unfenced response — strip any language-tag line that might
    #    be sitting at the top, then return the body.
    lines = text.splitlines()
    if lines and _FENCE_OPEN.match(lines[0].lstrip()):
        return _clean_fence_body("\n".join(lines[1:]).strip("\n"))

    return _strip_preamble(text)


def _clean_fence_body(body: str) -> str:
    """Strip the captured fence body, drop trailing backslash sequences
    that some LLM responses accidentally include, and remove any
    conversational preamble."""
    body = body.strip("\n")
    # A model sometimes leaves a literal "\\n" at the very end where
    # the closing backtick was rendered as escape. Drop one trailing
    # backslash or "\\n" sequence if present.
    while body.endswith(("\\\n", "\\\\")):
        body = body[:-2].rstrip()
    return _strip_preamble(body)


def _strip_preamble(body: str) -> str:
    """Drop a leading conversational line if present.

    Some models emit a sentence like "Sure, here is the implementation:"
    immediately before the code, even within a fenced block. We drop
    any contiguous non-code preamble lines from the start.
    """
    lines = body.splitlines()
    start = 0
    while start < len(lines):
        stripped = lines[start].strip().lower()
        if not stripped:
            start += 1
            continue
        if any(stripped.startswith(p) for p in _PREAMBLE_PREFIXES):
            start += 1
            continue
        # If we hit a line that looks like code (import, def, class,
        # decorator, comment), stop the preamble-stripping.
        if (
            stripped.startswith(("import ", "from ", "def ", "class ", "@", "#", "async ", "\"\"\""))
            or "=" in lines[start]
            or ":" in lines[start]
        ):
            break
        start += 1
    return "\n".join(lines[start:]).strip("\n")
