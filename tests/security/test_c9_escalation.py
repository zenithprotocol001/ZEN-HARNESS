"""C9 security tests: capability escalation attempts."""

import pytest

from dhc.cordis.context import Context
from dhc.modules.c5_agent_registry.service import (
    AgentManifest,
    apply as apply_registry,
    compute_signature,
)
from dhc.modules.c9_capability_policy.service import (
    CapabilityDenied,
    apply as apply_policy,
)


@pytest.mark.asyncio
async def test_c9_denies_escalation():
    ctx = Context()
    await apply_policy(ctx)
    await apply_registry(ctx, {"root_secret": b"super-secret-root-key-32bytes!!"})

    registry = ctx.inject("registry")
    caps = ["read_file"]
    sig = compute_signature(b"super-secret-root-key-32bytes!!", "weak_agent", caps)
    manifest = AgentManifest(agent_id="weak_agent", capabilities=caps, signature=sig)
    registry.register(manifest)

    # Sanity: read_file is allowed.
    await ctx.events.emit(
        "tools/pre-execute", {"agent_id": "weak_agent", "tool_name": "read_file"}
    )

    # Escalation: shell_execute is denied.
    with pytest.raises(CapabilityDenied):
        await ctx.events.emit(
            "tools/pre-execute", {"agent_id": "weak_agent", "tool_name": "shell_execute"}
        )


@pytest.mark.asyncio
async def test_c9_policy_module_has_no_grant_event_listener():
    """The policy must not auto-grant on any internal event other than
    explicit `grant()` calls from the registry."""
    ctx = Context()
    await apply_policy(ctx)
    policy = ctx.inject("policy")

    # Emit a forged "policy/grant" event; the policy must ignore it.
    listeners = ctx.events._listeners
    assert "policy/grant" not in listeners
    assert "capability/grant" not in listeners

    # And direct injection attempt is not supported.
    with pytest.raises(AttributeError):
        setattr(policy, "_agent_capabilities", {"attacker": {"*"}})


@pytest.mark.asyncio
async def test_c9_rejects_event_without_listener_path():
    """A payload that smuggles a 'grant' field must not be honored."""
    ctx = Context()
    await apply_policy(ctx)
    with pytest.raises(CapabilityDenied):
        await ctx.events.emit(
            "tools/pre-execute",
            {"agent_id": "x", "tool_name": "bash", "grant": True},
        )


@pytest.mark.asyncio
async def test_c9_deny_all_for_unknown_agents():
    ctx = Context()
    await apply_policy(ctx)
    with pytest.raises(CapabilityDenied):
        await ctx.events.emit(
            "tools/pre-execute", {"agent_id": "ghost", "tool_name": "read_file"}
        )
