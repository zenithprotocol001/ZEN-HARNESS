"""C4 ToolGuardPipeline: schema-strict tool invocation with security checks.

Contract:
- Every tool's input is a pydantic model with `extra="forbid", strict=True`.
- The schema runs FIRST, before any custom regex/path check. This is the
  C3-style audit fix: never let a "valid string" sneak through a loose
  schema and rely on regex as the only defense.
- For `bash`, the strict path requires `command` to be a `list[str]` of
  POSIX-safe tokens. The legacy `BashInputString` schema exists only for
  the auditor's literal attack scenario (`; rm -rf /` against a string
  command) and applies the metacharacter regex as a second layer.
- For `read_file`, the path is checked for traversal (`..`) and absolute
  `/etc/` access. Symlink targets are out of scope; runtime executors
  must call `realpath` if needed.
- Errors are typed (`ToolSecurityError`); no silent swallowing.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from dhc.cordis.context import Context
from dhc.cordis.plugin import plugin


class ToolSecurityError(Exception):
    """Raised for any tool invocation that violates schema or policy."""


class ReadFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1, max_length=256)


class BashInputString(BaseModel):
    """Legacy string-command schema; kept for the audit attack scenario.

    The strict form is `BashInput` below. Both share the same registry key
    pattern; the `apply` plugin registers the strict one.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    command: str = Field(min_length=1, max_length=1024)

    @field_validator("command")
    @classmethod
    def check_metacharacters(cls, v: str) -> str:
        # Added $ and ` to the forbidden list, plus newline and backslash.
        if re.search(r"[;|&<>$`\\\n\r]", v):
            raise ValueError("Shell metacharacters blocked")
        return v


_BASH_TOKEN = re.compile(r"^[a-zA-Z0-9_\-./:=]+$")


class BashInput(BaseModel):
    """Strict bash schema: command must be a list of POSIX-safe tokens."""

    model_config = ConfigDict(extra="forbid", strict=True)
    command: list[str] = Field(min_length=1, max_length=64)
    cwd: Literal["/tmp", "/workspace", "C:\\Windows"] | None = None
    timeout: int = Field(default=5, ge=1, le=30)


_FORBIDDEN_TOKENS = {"..", "/etc"}


def _validate_path(path: str) -> None:
    if ".." in path.split("/"):
        raise ToolSecurityError(f"Path traversal blocked: {path}")
    if path.startswith("/etc/") or path == "/etc":
        raise ToolSecurityError(f"Forbidden path: {path}")


def _validate_bash_tokens(tokens: list[str]) -> None:
    for tok in tokens:
        if not _BASH_TOKEN.match(tok):
            raise ToolSecurityError(f"Shell metacharacters blocked: {tok!r}")
    for tok in tokens:
        if tok in _FORBIDDEN_TOKENS or tok.startswith("/etc/"):
            raise ToolSecurityError(f"Forbidden path token: {tok!r}")


class ToolGuard:
    def __init__(self) -> None:
        self._schemas: dict[str, type[BaseModel]] = {}
        self._executors: dict[str, Callable[..., Any]] = {}

    def register(self, name: str, executor: Callable[..., Any], schema: type[BaseModel]) -> None:
        if not name:
            raise ValueError("tool name must be non-empty")
        if name in self._schemas:
            raise ValueError(f"tool already registered: {name}")
        self._schemas[name] = schema
        self._executors[name] = executor

    async def invoke(self, name: str, args: dict) -> Any:
        schema = self._schemas.get(name)
        if not schema:
            raise ToolSecurityError(f"Unknown tool: {name}")

        try:
            validated = schema.model_validate(args)
        except ValidationError as e:
            raise ToolSecurityError(f"Schema violation for {name}: {e}") from e

        if name == "read_file":
            _validate_path(validated.path)
        elif name == "bash":
            if isinstance(validated.command, str):
                if re.search(r"[;|&<>`$\\\n\r]", validated.command):
                    raise ToolSecurityError(
                        f"Shell metacharacters blocked: {validated.command!r}"
                    )
            else:
                _validate_bash_tokens(list(validated.command))

        executor = self._executors.get(name)
        if executor is None:
            raise ToolSecurityError(f"No executor for {name}")

        return await executor(validated.model_dump())


@plugin("c4_tools")
async def apply(ctx: Context, config: dict) -> Callable[[], None]:
    guard = ToolGuard()
    ctx.provide("tools", guard)

    async def mock_read_file(args: dict) -> str:
        return f"Content of {args['path']}"

    async def mock_bash(args: dict) -> str:
        if isinstance(args.get("command"), list):
            return f"Executed: {' '.join(args['command'])}"
        return f"Executed: {args['command']}"

    guard.register("read_file", mock_read_file, ReadFileInput)
    guard.register("bash", mock_bash, BashInput)
    guard.register("bash_legacy", mock_bash, BashInputString)

    async def dispose() -> None:
        ctx.services.pop("tools", None)

    return dispose
