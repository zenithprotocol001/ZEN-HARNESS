"""C5 security tests: spoofed signatures, scope smuggling, and timing."""

import hashlib
import hmac

import pytest

from dhc.cordis.context import Context
from dhc.modules.c5_agent_registry.service import (
    AgentManifest,
    UnauthorizedScope,
    apply,
    compute_signature,
)


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
async def test_c5_rejects_tampered_capabilities():
    """Re-signing with the same agent_id but modified capabilities must fail."""
    ctx = Context()
    secret = b"super-secret-root-key-32bytes!!"
    await apply(ctx, {"root_secret": secret})
    registry = ctx.inject("registry")
    sig = compute_signature(secret, "agent_x", ["read_file"])
    tampered = AgentManifest(
        agent_id="agent_x",
        capabilities=["read_file", "shell_execute"],
        signature=sig,
    )
    with pytest.raises(UnauthorizedScope):
        registry.register(tampered)


@pytest.mark.asyncio
async def test_c5_rejects_unicode_separator_smuggle():
    """A capability string containing the canonical separator (0x1F) must
    be rejected at registration time, not silently split."""
    ctx = Context()
    secret = b"super-secret-root-key-32bytes!!"
    await apply(ctx, {"root_secret": secret})
    with pytest.raises(Exception):
        AgentManifest(
            agent_id="evil",
            capabilities=["ok\x1fadmin"],
            signature="0" * 64,
        )


@pytest.mark.asyncio
async def test_c5_unregistered_agent_cannot_use_tools():
    """An agent not in the registry has no capabilities and is denied."""
    from dhc.modules.c9_capability_policy.service import apply as apply_policy

    ctx = Context()
    await apply_policy(ctx)
    await apply(ctx, {"root_secret": b"super-secret-root-key-32bytes!!"})
    policy = ctx.inject("policy")
    with pytest.raises(Exception):
        policy.check("ghost", "read_file")


@pytest.mark.asyncio
async def test_c5_timing_safe_verification():
    """The C5 module must use hmac.compare_digest, not `==`."""
    from pathlib import Path

    import ast

    src_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "dhc"
        / "modules"
        / "c5_agent_registry"
        / "service.py"
    )
    text = src_path.read_text(encoding="utf-8")
    assert "hmac.compare_digest" in text

    # No plain == on hmac-shaped names. We walk the AST and inspect only
    # Compare nodes whose operands are ast.Name with hmac-shaped names,
    # ignoring comments and docstrings.
    tree = ast.parse(text, filename=str(src_path))

    def is_hmac_shaped(node: ast.AST) -> bool:
        if not isinstance(node, ast.Name):
            return False
        name = node.id.lower()
        targets = {
            "expected",
            "provided",
            "digest",
            "signature",
            "computed",
            "sig",
            "mac",
            "hash",
            "hmac",
        }
        if name in targets:
            return True
        return any(tok in name for tok in ("hmac", "sig", "mac", "hash", "digest"))

    suspicious: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for op in node.ops:
                if not isinstance(op, ast.Eq):
                    continue
                left = node.left
                right = node.comparators[0] if node.comparators else None
                if is_hmac_shaped(left) and is_hmac_shaped(right):
                    suspicious.append((node.lineno, ast.unparse(node)))
    assert not suspicious, f"plain == on hmac-shaped names: {suspicious}"
