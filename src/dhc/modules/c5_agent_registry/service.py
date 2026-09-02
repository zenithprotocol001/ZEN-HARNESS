"""C5 AgentRegistry: HMAC-SHA256 signed agent manifests with scope isolation.

Contract:
- The registry is initialized with a `root_secret` (provided by the host,
  not the network). It signs manifests only for agents whose HMAC matches
  the expected digest over a fixed canonical form.
- `AgentManifest.signature` is a hex sha256 string. Verification uses
  `hmac.compare_digest` (constant time) — never `==`.
- Canonical form is bytes: `agent_id_bytes || 0x1F || sorted(caps_joined_by_0x1F)`.
  Using a 0x1F (Unit Separator) delimiter ensures agent_ids cannot smuggle
  a `|` character into a different capability. (C8 lesson: pin canonical
  form to bytes, not str.)
- Successful registration auto-grants capabilities via the C9 policy if
  it has been wired into the same `Context`.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dhc.cordis.context import Context
from dhc.cordis.plugin import plugin

_CANONICAL_SEP = b"\x1f"


class UnauthorizedScope(Exception):
    """Raised when an agent manifest fails HMAC verification."""


class AgentManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    agent_id: str = Field(min_length=1, max_length=64)
    capabilities: list[str] = Field(min_length=1)
    signature: str = Field(min_length=64, max_length=64)

    @field_validator("capabilities", mode="before")
    @classmethod
    def check_no_separator(cls, v: list[str]) -> list[str]:
        for cap in v:
            if "\x1f" in cap:
                raise ValueError("capability contains forbidden 0x1F separator")
        return v


def compute_signature(secret: bytes, agent_id: str, capabilities: list[str]) -> str:
    aid = agent_id.encode("utf-8")
    if not aid:
        raise ValueError("agent_id must be non-empty")
    for cap in capabilities:
        if not cap or "\x1f" in cap:
            raise ValueError(f"invalid capability: {cap!r}")
    sorted_caps = sorted(capabilities)
    payload = aid + _CANONICAL_SEP + _CANONICAL_SEP.join(c.encode("utf-8") for c in sorted_caps)
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def verify_signature(secret: bytes, agent_id: str, capabilities: list[str], provided: str) -> None:
    expected = compute_signature(secret, agent_id, capabilities)
    if not hmac.compare_digest(expected, provided):
        raise UnauthorizedScope(f"Invalid signature for agent {agent_id!r}")


class AgentRegistry:
    def __init__(self, root_secret: bytes, ctx: Context) -> None:
        if not root_secret or len(root_secret) < 16:
            raise ValueError("root_secret must be at least 16 bytes")
        self._secret = root_secret
        self._ctx = ctx
        self._registered: dict[str, AgentManifest] = {}

    def register(self, manifest: AgentManifest) -> AgentManifest:
        verify_signature(self._secret, manifest.agent_id, manifest.capabilities, manifest.signature)
        self._registered[manifest.agent_id] = manifest

        policy = self._ctx.inject("policy")
        if policy is not None:
            for cap in manifest.capabilities:
                policy.grant(manifest.agent_id, cap)
        return manifest

    def is_registered(self, agent_id: str) -> bool:
        return agent_id in self._registered

    def get(self, agent_id: str) -> AgentManifest | None:
        return self._registered.get(agent_id)

    def unregister(self, agent_id: str) -> None:
        self._registered.pop(agent_id, None)


@plugin("c5_registry")
async def apply(ctx: Context, config: dict) -> Callable[[], None]:
    secret = (config or {}).get("root_secret") or b"x" * 32
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    registry = AgentRegistry(root_secret=secret, ctx=ctx)
    ctx.provide("registry", registry)

    async def dispose() -> None:
        ctx.services.pop("registry", None)

    return dispose
