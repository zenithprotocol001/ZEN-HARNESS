"""Unit tests for the five bundled plugins (async path)."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from dhc.cordis.context import Context
from dhc.plugins.loader import PluginState, discover, load_async, unload


# ---------- rate_limiter_v1 ----------


@pytest.mark.asyncio
async def test_rate_limiter_allows_within_burst():
    state = PluginState()
    state.discovered = discover()
    ctx = Context()
    await load_async(state, ctx, "rate_limiter_v1", config={"rate": 1.0, "burst": 3.0})
    limiter = ctx.inject("rate_limiter")
    for _ in range(3):
        limiter.check("a1")  # should not raise
    with pytest.raises(Exception):
        limiter.check("a1")  # 4th within the same instant exceeds burst
    await unload(state, ctx, "rate_limiter_v1")


@pytest.mark.asyncio
async def test_rate_limiter_refills_over_time():
    state = PluginState()
    state.discovered = discover()
    ctx = Context()
    await load_async(state, ctx, "rate_limiter_v1", config={"rate": 100.0, "burst": 2.0})
    limiter = ctx.inject("rate_limiter")
    limiter.check("a1")
    limiter.check("a1")
    with pytest.raises(Exception):
        limiter.check("a1")
    # Wait for refill
    await asyncio.sleep(0.05)
    limiter.check("a1")  # 50ms at 100 tps = 5 tokens, plenty
    await unload(state, ctx, "rate_limiter_v1")


@pytest.mark.asyncio
async def test_rate_limiter_intercepts_pre_execute():
    state = PluginState()
    state.discovered = discover()
    ctx = Context()
    await load_async(state, ctx, "rate_limiter_v1", config={"rate": 1.0, "burst": 1.0})
    limiter = ctx.inject("rate_limiter")
    limiter.check("a1")
    throttled: list[dict] = []

    async def capture(payload):
        throttled.append(payload)

    ctx.events.on("system/throttled", capture)
    await ctx.events.emit("tools/pre-execute", {"agent_id": "a1", "tool_name": "x"})
    assert len(throttled) == 1
    assert throttled[0]["agent_id"] == "a1"
    await unload(state, ctx, "rate_limiter_v1")


# ---------- session_exporter_v1 ----------


@pytest.mark.asyncio
async def test_session_exporter_writes_ndjson(tmp_path):
    from dhc.modules.c2_session_event_log.service import apply as apply_c2

    state = PluginState()
    state.discovered = discover()
    ctx = Context()
    # The plugin emits `turn/end`; C2 mirrors lifecycle events into
    # its log, so we apply c2 first.
    await apply_c2(ctx, {"heartbeat_ring_size": 8})

    out_dir = tmp_path / "sessions"
    await load_async(
        state,
        ctx,
        "session_exporter_v1",
        config={"out_dir": str(out_dir)},
    )

    # Drive a tiny turn to populate C2.
    await ctx.events.emit("turn/start", {"agent_id": "a1", "turn_id": "t-1"})
    await ctx.events.emit(
        "step/start", {"agent_id": "a1", "turn_id": "t-1", "step": 0}
    )
    await ctx.events.emit(
        "turn/end",
        {"agent_id": "a1", "turn_id": "t-1", "reason": "completed", "steps": 1},
    )
    # Give the listener a tick to drain.
    await asyncio.sleep(0)

    files = list(out_dir.glob("*.ndjson"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    parsed = [json.loads(ln) for ln in lines]
    assert parsed[0]["type"] == "turn/start"
    assert parsed[1]["type"] == "step/start"
    assert parsed[2]["type"] == "turn/end"
    await unload(state, ctx, "session_exporter_v1")


# ---------- model_router_v1 ----------


@pytest.mark.asyncio
async def test_model_router_tags_stream_events():
    state = PluginState()
    state.discovered = discover()
    ctx = Context()
    await load_async(
        state,
        ctx,
        "model_router_v1",
        config={"default": "primary", "routing": {"a1": "fast"}},
    )
    captured: list[dict] = []

    async def listener(payload):
        captured.append(payload)

    ctx.events.on("llm/stream", listener)
    await ctx.events.emit(
        "llm/stream", {"agent_id": "a1", "turn_id": "t-1", "content": "hi"}
    )
    await ctx.events.emit(
        "llm/stream", {"agent_id": "a2", "turn_id": "t-1", "content": "hi"}
    )

    assert captured[0].get("router") == "fast"  # a1 override
    assert captured[1].get("router") == "primary"  # a2 default
    await unload(state, ctx, "model_router_v1")


# ---------- memory_store_v1 ----------


@pytest.mark.asyncio
async def test_memory_store_put_get_list():
    state = PluginState()
    state.discovered = discover()
    ctx = Context()
    await load_async(state, ctx, "memory_store_v1", config={"cap_per_agent": 4})
    store = ctx.inject("memory_store")
    await ctx.events.emit(
        "memory/store",
        {"agent_id": "a1", "key": "topic", "value": {"note": "hello"}},
    )
    await ctx.events.emit(
        "memory/store",
        {"agent_id": "a1", "key": "lang", "value": "en"},
    )
    assert store.get("a1", "topic") == {"note": "hello"}
    assert store.get("a1", "lang") == "en"
    assert sorted(store.list("a1")) == ["lang", "topic"]
    await unload(state, ctx, "memory_store_v1")


@pytest.mark.asyncio
async def test_memory_store_recall_emits_result():
    state = PluginState()
    state.discovered = discover()
    ctx = Context()
    await load_async(state, ctx, "memory_store_v1", config={})
    store = ctx.inject("memory_store")
    results: list[dict] = []

    async def listener(payload):
        results.append(payload)

    ctx.events.on("memory/result", listener)
    await ctx.events.emit(
        "memory/store",
        {"agent_id": "a1", "key": "k", "value": 42},
    )
    await ctx.events.emit("memory/recall", {"agent_id": "a1", "key": "k"})
    await ctx.events.emit("memory/recall", {"agent_id": "a1", "key": "missing"})
    assert results[0]["found"] is True
    assert results[0]["value"] == 42
    assert results[1]["found"] is False
    await unload(state, ctx, "memory_store_v1")


# ---------- prompt_browser_v1 ----------


@pytest.mark.asyncio
async def test_prompt_browser_lists_and_retrieves_prompts():
    state = PluginState()
    state.discovered = discover()
    ctx = Context()
    await load_async(state, ctx, "prompt_browser_v1", config={})
    browser = ctx.inject("prompt_browser")
    items = browser.list()
    assert isinstance(items, list)
    assert any(i["key"] == "c3_prompt_assembler" for i in items)
    body = browser.get("c3_prompt_assembler")
    assert isinstance(body, str) and "PromptAssembler" in body
    await unload(state, ctx, "prompt_browser_v1")
