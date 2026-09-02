"""Test that unloading a plugin fully detaches its event listeners."""

from __future__ import annotations

import pytest

from dhc.cordis.context import Context
from dhc.plugins.loader import PluginState, discover, load_async, unload


@pytest.mark.asyncio
async def test_unload_drops_event_listeners():
    state = PluginState()
    state.discovered = discover()
    ctx = Context()

    # model_router_v1 registers a listener on llm/stream.
    await load_async(state, ctx, "model_router_v1", config={})
    pre_listeners = len(ctx.events._listeners.get("llm/stream", []))
    assert pre_listeners >= 1

    # Unload
    await unload(state, ctx, "model_router_v1")
    post_listeners = len(ctx.events._listeners.get("llm/stream", []))
    assert post_listeners < pre_listeners


@pytest.mark.asyncio
async def test_unload_drops_event_listeners_rate_limiter():
    state = PluginState()
    state.discovered = discover()
    ctx = Context()
    await load_async(state, ctx, "rate_limiter_v1", config={"rate": 1.0, "burst": 1.0})
    assert len(ctx.events._listeners.get("tools/pre-execute", [])) >= 1
    await unload(state, ctx, "rate_limiter_v1")
    assert len(ctx.events._listeners.get("tools/pre-execute", [])) == 0


@pytest.mark.asyncio
async def test_reload_does_not_double_register_listeners():
    """If a plugin is unloaded and reloaded, the second load must NOT
    leave two listeners attached (which would double-bill events)."""
    state = PluginState()
    state.discovered = discover()
    ctx = Context()
    await load_async(state, ctx, "model_router_v1", config={})
    n1 = len(ctx.events._listeners.get("llm/stream", []))
    await unload(state, ctx, "model_router_v1")
    await load_async(state, ctx, "model_router_v1", config={})
    n2 = len(ctx.events._listeners.get("llm/stream", []))
    assert n1 == n2, f"expected {n1} listeners after reload, got {n2}"
