"""C4 unit tests: schema enforcement and registration plumbing."""

import pytest

from dhc.cordis.context import Context
from dhc.modules.c4_tool_guard_pipeline.service import (
    BashInput,
    ReadFileInput,
    ToolGuard,
    ToolSecurityError,
    apply,
)


@pytest.mark.asyncio
async def test_c4_registers_read_file_and_bash():
    ctx = Context()
    await apply(ctx)
    guard = ctx.inject("tools")
    assert isinstance(guard, ToolGuard)
    assert "read_file" in guard._schemas
    assert "bash" in guard._schemas


@pytest.mark.asyncio
async def test_c4_strict_bash_rejects_string_command_at_schema():
    """The strict `bash` schema requires `command: list[str]`. A string
    command (the auditor's literal attack) must be rejected by the schema,
    not by the regex layer."""
    ctx = Context()
    await apply(ctx)
    guard = ctx.inject("tools")
    with pytest.raises(ToolSecurityError) as excinfo:
        await guard.invoke("bash", {"command": "; rm -rf /"})
    assert "Schema violation" in str(excinfo.value)


@pytest.mark.asyncio
async def test_c4_strict_bash_accepts_safe_token_list():
    ctx = Context()
    await apply(ctx)
    guard = ctx.inject("tools")
    out = await guard.invoke("bash", {"command": ["ls", "-la", "/workspace"]})
    assert "ls" in out and "-la" in out


@pytest.mark.asyncio
async def test_c4_strict_bash_rejects_unsafe_tokens():
    ctx = Context()
    await apply(ctx)
    guard = ctx.inject("tools")
    with pytest.raises(ToolSecurityError):
        await guard.invoke("bash", {"command": ["ls", ";", "rm", "-rf", "/"]})


@pytest.mark.asyncio
async def test_c4_strict_bash_enforces_cwd_and_timeout():
    ctx = Context()
    await apply(ctx)
    guard = ctx.inject("tools")
    with pytest.raises(ToolSecurityError):
        await guard.invoke("bash", {"command": ["ls"], "cwd": "C:\\\\Windows"})
    with pytest.raises(ToolSecurityError):
        await guard.invoke("bash", {"command": ["ls"], "timeout": 99999})


@pytest.mark.asyncio
async def test_c4_read_file_accepts_workspace_path():
    ctx = Context()
    await apply(ctx)
    guard = ctx.inject("tools")
    out = await guard.invoke("read_file", {"path": "src/dhc/__init__.py"})
    assert "src/dhc/__init__.py" in out


@pytest.mark.asyncio
async def test_c4_unknown_tool_rejected():
    ctx = Context()
    await apply(ctx)
    guard = ctx.inject("tools")
    with pytest.raises(ToolSecurityError):
        await guard.invoke("nuke_planet", {"x": 1})


@pytest.mark.asyncio
async def test_c4_extra_field_rejected():
    ctx = Context()
    await apply(ctx)
    guard = ctx.inject("tools")
    with pytest.raises(ToolSecurityError):
        await guard.invoke("read_file", {"path": "x", "injected": "y"})


def test_c4_schemas_strict():
    assert ReadFileInput.model_config.get("extra") == "forbid"
    assert ReadFileInput.model_config.get("strict") is True
    assert BashInput.model_config.get("extra") == "forbid"
    assert BashInput.model_config.get("strict") is True
