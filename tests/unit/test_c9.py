"""C9 unit tests: deny-all default, grant/revoke, pre-execute interception."""

import pytest

from dhc.cordis.context import Context
from dhc.modules.c9_capability_policy.service import (
    CapabilityDenied,
    CapabilityPolicy,
    apply,
)


@pytest.mark.asyncio
async def test_c9_deny_all_default():
    ctx = Context()
    await apply(ctx)
    policy = ctx.inject("policy")
    with pytest.raises(CapabilityDenied):
        policy.check("unknown_agent", "any_tool")


@pytest.mark.asyncio
async def test_c9_grant_allows_exact_tool():
    ctx = Context()
    await apply(ctx)
    policy = ctx.inject("policy")
    policy.grant("agent_a", "read_file")
    policy.check("agent_a", "read_file")
    with pytest.raises(CapabilityDenied):
        policy.check("agent_a", "shell_execute")


@pytest.mark.asyncio
async def test_c9_revoke_removes_capability():
    ctx = Context()
    await apply(ctx)
    policy = ctx.inject("policy")
    policy.grant("a", "read_file")
    policy.revoke("a", "read_file")
    with pytest.raises(CapabilityDenied):
        policy.check("a", "read_file")


@pytest.mark.asyncio
async def test_c9_pre_execute_event_intercepts():
    ctx = Context()
    await apply(ctx)
    policy = ctx.inject("policy")
    policy.grant("agent_a", "read_file")

    await ctx.events.emit("tools/pre-execute", {"agent_id": "agent_a", "tool_name": "read_file"})

    with pytest.raises(CapabilityDenied):
        await ctx.events.emit("tools/pre-execute", {"agent_id": "agent_a", "tool_name": "bash"})


@pytest.mark.asyncio
async def test_c9_pre_execute_rejects_malformed_payload():
    ctx = Context()
    await apply(ctx)
    with pytest.raises(CapabilityDenied):
        await ctx.events.emit("tools/pre-execute", {"agent_id": "x", "extra": "y"})


def test_c9_empty_inputs_rejected():
    p = CapabilityPolicy()
    with pytest.raises(ValueError):
        p.grant("", "read_file")
    with pytest.raises(ValueError):
        p.grant("a", "")
