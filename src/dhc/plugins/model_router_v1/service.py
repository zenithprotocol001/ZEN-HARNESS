"""model_router_v1 — per-agent LLM router.

Reads the existing `ctx.inject("llm")` (a C7 LLMStreamAdapter) and
re-exports a `ModelRouter` whose `pick(agent_id)` returns a tuple
`(name, adapter)`. The router records the selection on each
`llm/stream` event into the C2 session log (visible as
`{"router": "name"}` in the event payload).

Configuration:
    {"default": "primary", "routing": {"alice": "fast", "bob": "deep"}}

The router is read-only: it observes the existing `ctx.services["llm"]`
and tags streams. To actually pick a different adapter per agent,
the C6 driver or the GUI must call `router.pick(agent_id)` and use
the returned adapter. The plugin exposes `ctx.inject("model_router")`.
"""

from __future__ import annotations

from typing import Any, Callable

from dhc.cordis.context import Context
from dhc.cordis.plugin import plugin


class ModelRouter:
    """Selects an LLM adapter per agent_id based on a static config."""

    def __init__(self, default: str = "primary", routing: dict | None = None) -> None:
        self._default = str(default)
        self._routing: dict[str, str] = dict(routing or {})
        self._last_seen: dict[str, str] = {}

    def pick(self, agent_id: str) -> tuple[str, Any]:
        """Return the (name, adapter) pair for an agent.

        The adapter is whatever is currently registered as
        `ctx.inject("llm")`; the router only contributes a name.
        Different adapters are out of scope for this v1 plugin.
        """
        return self._routing.get(agent_id, self._default), None

    def record(self, agent_id: str) -> str:
        """Return the name to embed in an `llm/stream` payload."""
        name, _ = self.pick(agent_id)
        self._last_seen[agent_id] = name
        return name

    def snapshot(self) -> dict[str, str]:
        return dict(self._last_seen)


@plugin("model_router_v1")
async def apply(ctx: Context, config: dict) -> Callable[[], None]:
    router = ModelRouter(
        default=str(config.get("default", "primary")) if config else "primary",
        routing=dict(config.get("routing", {})) if config else {},
    )
    ctx.provide("model_router", router)

    async def on_llm_stream(payload: dict) -> None:
        agent_id = str(payload.get("agent_id") or "")
        if not agent_id:
            return
        name = router.record(agent_id)
        # Merge so we don't clobber a pre-existing key.
        if "router" not in payload:
            payload["router"] = name

    ctx.events.on("llm/stream", on_llm_stream)

    async def dispose() -> None:
        ctx.events.off("llm/stream", on_llm_stream)
        ctx.services.pop("model_router", None)

    return dispose
