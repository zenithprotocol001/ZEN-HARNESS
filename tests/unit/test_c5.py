"""C5 unit tests: HMAC verification, scope isolation, and auto-grant."""

import pytest

from dhc.cordis.context import Context
from dhc.modules.c5_agent_registry.service import (
    AgentManifest,
    AgentRegistry,
    UnauthorizedScope,
    apply,
    compute_signature,
    verify_signature,
)


def test_c5_compute_signature_is_deterministic():
    s1 = compute_signature(b"k" * 32, "agent_x", ["read_file", "bash"])
    s2 = compute_signature(b"k" * 32, "agent_x", ["bash", "read_file"])
    assert s1 == s2


def test_c5_compute_signature_differs_by_capabilities():
    s1 = compute_signature(b"k" * 32, "agent_x", ["read_file"])
    s2 = compute_signature(b"k" * 32, "agent_x", ["read_file", "bash"])
    assert s1 != s2


def test_c5_compute_signature_rejects_unsafe_capability():
    with pytest.raises(ValueError):
        compute_signature(b"k" * 32, "agent_x", ["ok\x1fbad"])


@pytest.mark.asyncio
async def test_c5_rejects_short_secret():
    ctx = Context()
    with pytest.raises(ValueError):
        await apply(ctx, {"root_secret": b"short"})


@pytest.mark.asyncio
async def test_c5_rejects_spoofed_signature():
    ctx = Context()
    await apply(ctx, {"root_secret": b"super-secret-root-key-32bytes!!"})
    registry = ctx.inject("registry")
    spoofed = AgentManifest(
        agent_id="attacker",
        capabilities=["shell_execute", "admin_access"],
        signature="0" * 64,
    )
    with pytest.raises(UnauthorizedScope):
        registry.register(spoofed)
    assert not registry.is_registered("attacker")


@pytest.mark.asyncio
async def test_c5_accepts_valid_signature():
    ctx = Context()
    secret = b"super-secret-root-key-32bytes!!"
    await apply(ctx, {"root_secret": secret})
    registry = ctx.inject("registry")
    sig = compute_signature(secret, "good_agent", ["read_file"])
    manifest = AgentManifest(agent_id="good_agent", capabilities=["read_file"], signature=sig)
    registry.register(manifest)
    assert registry.is_registered("good_agent")


@pytest.mark.asyncio
async def test_c5_auto_grants_via_policy():
    ctx = Context()
    from dhc.modules.c9_capability_policy.service import apply as apply_policy

    secret = b"super-secret-root-key-32bytes!!"
    await apply_policy(ctx)
    await apply(ctx, {"root_secret": secret})
    policy = ctx.inject("policy")
    registry = ctx.inject("registry")
    sig = compute_signature(secret, "a1", ["read_file", "bash"])
    manifest = AgentManifest(agent_id="a1", capabilities=["read_file", "bash"], signature=sig)
    registry.register(manifest)
    policy.check("a1", "read_file")
    policy.check("a1", "bash")


@pytest.mark.asyncio
async def test_c5_extra_field_rejected():
    ctx = Context()
    await apply(ctx, {"root_secret": b"super-secret-root-key-32bytes!!"})
    with pytest.raises(Exception):
        AgentManifest(
            agent_id="x",
            capabilities=["read_file"],
            signature="0" * 64,
            scope="admin",
        )
