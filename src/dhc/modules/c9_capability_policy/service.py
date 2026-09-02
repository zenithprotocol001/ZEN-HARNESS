"""C9 CapabilityPolicy: deny-all by default, intercepts `tools/pre-execute`.

Contract:
- An agent with no explicit grant cannot run any tool. This is the
  deny-all default.
- A registered agent can run only the tools whose names appear in its
  capability set. The set is plain strings; no wildcards, no hierarchies.
- A registered agent cannot escalate its own capabilities at runtime;
  `grant` is the only mutator and is invoked exclusively by the C5
  registry (or by an explicit privileged caller). There is no
  `add_capability` event listener.
- The `tools/pre-execute` event payload is read as a typed pydantic model
  to prevent extra-field smuggling.
"""

from __future__ import annotations

from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from dhc.cordis.context import Context
from dhc.cordis.plugin import plugin


class CapabilityDenied(Exception):
    """Raised when an agent attempts a tool it has not been granted."""


class PreExecutePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    agent_id: str = Field(min_length=1, max_length=64)
    tool_name: str = Field(min_length=1, max_length=64)


class CapabilityPolicy:
    __slots__ = ("__caps",)

    def __init__(self) -> None:
        # The actual dict is stored under a name-mangled slot so external
        # code that does `setattr(policy, "_agent_capabilities", ...)`
        # cannot find it (and gets AttributeError). The internal accessors
        # use the mangled name explicitly.
        object.__setattr__(self, "_CapabilityPolicy__caps", {})

    def _caps(self) -> dict:
        return object.__getattribute__(self, "_CapabilityPolicy__caps")

    def grant(self, agent_id: str, capability: str) -> None:
        if not agent_id or not capability:
            raise ValueError("agent_id and capability must be non-empty")
        self._caps().setdefault(agent_id, set()).add(capability)

    def revoke(self, agent_id: str, capability: str) -> None:
        caps = self._caps()
        if agent_id in caps:
            caps[agent_id].discard(capability)

    def check(self, agent_id: str, tool_name: str) -> None:
        allowed = self._caps().get(agent_id, set())
        if tool_name not in allowed:
            raise CapabilityDenied(
                f"Agent {agent_id!r} lacks capability for tool {tool_name!r}"
            )

    def capabilities_of(self, agent_id: str) -> set[str]:
        return set(self._caps().get(agent_id, set()))


@plugin("c9_policy")
async def apply(ctx: Context, config: dict) -> Callable[[], None]:
    policy = CapabilityPolicy()
    ctx.provide("policy", policy)

    async def pre_execute_hook(payload: dict) -> None:
        # Validate payload as a strict pydantic model.
        try:
            p = PreExecutePayload.model_validate(payload)
        except Exception as exc:
            raise CapabilityDenied(f"malformed pre-execute payload: {exc}") from exc
        policy.check(p.agent_id, p.tool_name)

    ctx.events.on("tools/pre-execute", pre_execute_hook)

    async def dispose() -> None:
        ctx.events.off("tools/pre-execute", pre_execute_hook)
        ctx.services.pop("policy", None)

    return dispose
