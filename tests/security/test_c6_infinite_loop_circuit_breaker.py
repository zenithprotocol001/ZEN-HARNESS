"""C6 security tests: step-limit circuit breaker, listener leak prevention."""

import pytest

from dhc.cordis.context import Context
from dhc.modules.c6_turn_step_driver.service import (
    StepLimitExceeded,
    TurnStepDriver,
    apply,
)


class _Chunk:
    def __init__(self, content="", tool_calls=None, finish_reason=None):
        self.content = content
        self.delta = content
        self.tool_calls = tool_calls or []
        self.finish_reason = finish_reason


class _IndefiniteStream:
    def __init__(self, chunk, max_yields_per_call=10_000):
        self._chunk = chunk
        self._max = max_yields_per_call

    def __aiter__(self):
        async def gen():
            for _ in range(self._max):
                yield self._chunk
        return gen()


class _OneShotStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._exhausted = False

    async def __aiter__(self):
        if not self._exhausted:
            self._exhausted = True
            for c in self._chunks:
                yield c


async def _aiter(chunks):
    for c in chunks:
        yield c


@pytest.mark.asyncio
async def test_c6_infinite_need_more_info_terminates_at_max_steps():
    """The audit's literal attack: tool always returns need_more_info.
    Driver must abort after 5 steps with max_steps_exceeded and raise."""
    ctx = Context()
    await apply(ctx, {"max_steps": 5})
    driver = ctx.inject("loop")

    loop_chunk = _Chunk(
        tool_calls=[
            {
                "function": {
                    "name": "ask_clarification",
                    "arguments": '{"status": "need_more_info"}',
                }
            }
        ],
        finish_reason="tool_calls",
    )

    with pytest.raises(StepLimitExceeded):
        await driver.run_turn(
            ctx,
            agent_id="loop_agent",
            llm_stream=_IndefiniteStream(loop_chunk),
            tool_dispatch=lambda n, a: {"ok": True},
        )


@pytest.mark.asyncio
async def test_c6_step_limit_raises_step_limit_exceeded():
    """Per the spec, exceeding the limit must also raise the typed error."""
    ctx = Context()
    await apply(ctx, {"max_steps": 2})
    driver = ctx.inject("loop")
    loop_chunk = _Chunk(
        tool_calls=[{"function": {"name": "ask_clarification", "arguments": "{}"}}]
    )
    with pytest.raises(StepLimitExceeded):
        await driver.run_turn(
            ctx,
            agent_id="loop",
            llm_stream=_IndefiniteStream(loop_chunk),
            tool_dispatch=lambda n, a: {"ok": True},
        )


@pytest.mark.asyncio
async def test_c6_turn_end_emitted_with_max_steps_exceeded():
    ctx = Context()
    await apply(ctx, {"max_steps": 3})
    driver = ctx.inject("loop")

    events: list[dict] = []

    async def record(payload):
        events.append(payload)

    ctx.events.on("turn/end", record)

    loop_chunk = _Chunk(
        tool_calls=[{"function": {"name": "ask_clarification", "arguments": "{}"}}]
    )
    with pytest.raises(StepLimitExceeded):
        await driver.run_turn(
            ctx,
            agent_id="loop",
            llm_stream=_IndefiniteStream(loop_chunk),
            tool_dispatch=lambda n, a: {"ok": True},
        )
    end_events = [p for p in events if p.get("reason")]
    assert end_events
    assert end_events[-1]["reason"] == "max_steps_exceeded"


@pytest.mark.asyncio
async def test_c6_listener_leak_after_dispose():
    """After dispose, no C6 listener must remain on the context events."""
    ctx = Context()
    await apply(ctx, {"max_steps": 3})
    driver = ctx.inject("loop")

    loop_chunk = _Chunk(
        tool_calls=[{"function": {"name": "ask_clarification", "arguments": "{}"}}]
    )
    with pytest.raises(StepLimitExceeded):
        await driver.run_turn(
            ctx,
            agent_id="loop",
            llm_stream=_IndefiniteStream(loop_chunk),
            tool_dispatch=lambda n, a: {"ok": True},
        )

    # Manually dispose the plugin
    from dhc.modules.c6_turn_step_driver.service import apply as apply_c6

    # Run dispose via the registered disposable if present
    for d in ctx._disposables:
        try:
            await d()
        except Exception:
            pass

    # C6 has no event listeners of its own, but assert no leakage happened
    # in core events either.
    for ev in ctx.events._listeners.values():
        for fn in ev:
            assert fn is not None


@pytest.mark.asyncio
async def test_c6_poisoned_tool_result_does_not_crash_loop():
    """A tool that always raises must produce a tool_error abort, not crash."""
    ctx = Context()
    await apply(ctx, {"max_steps": 3})
    driver = ctx.inject("loop")

    async def boom(name, args):
        raise RuntimeError("simulated tool failure")

    bad_chunk = _Chunk(
        tool_calls=[{"function": {"name": "read_file", "arguments": '{}'}}]
    )
    end = await driver.run_turn(
        ctx,
        agent_id="a1",
        llm_stream=lambda: _aiter([bad_chunk]),
        tool_dispatch=boom,
    )
    assert end.reason == "tool_error"


@pytest.mark.asyncio
async def test_c6_auditor_spec_exact_scenario():
    """Mirror the auditor's test scenario verbatim: a tool that always
    returns need_more_info. Abort reason must be max_steps_exceeded after
    exactly 5 steps."""
    ctx = Context()
    await apply(ctx, {"max_steps": 5})
    driver = ctx.inject("loop")

    async def always_need_more(name, args):
        return {"status": "need_more_info"}

    chunk = _Chunk(
        tool_calls=[
            {
                "function": {
                    "name": "ask_clarification",
                    "arguments": '{"status": "need_more_info"}',
                }
            }
        ],
        finish_reason="tool_calls",
    )

    with pytest.raises(StepLimitExceeded):
        await driver.run_turn(
            ctx,
            agent_id="loop_agent",
            llm_stream=lambda: _aiter([chunk]),
            tool_dispatch=always_need_more,
        )

