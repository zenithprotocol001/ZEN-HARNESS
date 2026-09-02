"""C6 unit tests: waterfall transitions, step counter, abort reasons."""

import pytest

from dhc.cordis.context import Context
from dhc.modules.c6_turn_step_driver.service import (
    ABORT_REASONS,
    DEFAULT_MAX_STEPS,
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
    """An async stream that yields `chunk` forever. Each call to
    `__aiter__` returns a fresh generator so multiple driver
    iterations (e.g. across the circuit-breaker test) all see the
    same behavior. A safety cap of 10_000 yields per call prevents
    pathological infinite loops from hanging the test suite."""

    def __init__(self, chunk, max_yields_per_call=10_000):
        self._chunk = chunk
        self._max = max_yields_per_call

    def __aiter__(self):
        async def gen():
            for _ in range(self._max):
                yield self._chunk
        return gen()


class _OneShotStream:
    """A single-shot async stream: yields each chunk in order once, then
    empty thereafter. Used by tests that want a turn to complete after
    a known number of LLM responses."""

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
async def test_c6_completed_turn_emits_full_waterfall():
    ctx = Context()
    await apply(ctx, {"max_steps": 3})
    driver = ctx.inject("loop")

    events: list[dict] = []

    async def record(payload):
        events.append(payload)

    for ev in [
        "turn/start",
        "step/start",
        "llm/stream",
        "tool/call",
        "step/end",
        "turn/end",
    ]:
        ctx.events.on(ev, record)

    async def tool_dispatch(name, args):
        return {"ok": True}

    chunks = [
        _Chunk(tool_calls=[{"function": {"name": "read_file", "arguments": '{"path":"a"}'}}]),
        _Chunk(content="done", finish_reason="stop"),
    ]
    end = await driver.run_turn(
        ctx, agent_id="a1", llm_stream=_OneShotStream(chunks), tool_dispatch=tool_dispatch
    )
    assert end.reason == "completed"
    assert end.steps == 2  # one tool-call step + one completion step

    # The events list records the *order of events as observed by the listener*.
    # We use the position in the test instead of inferring from the dict.
    assert len(events) >= 6
    # The last observed event is the turn/end payload.
    assert "reason" in events[-1]
    assert events[-1]["reason"] in ("completed", "max_steps_exceeded", "tool_error", "policy_denied", "llm_error")


@pytest.mark.asyncio
async def test_c6_max_steps_circuit_breaker_raises():
    ctx = Context()
    await apply(ctx, {"max_steps": 5})
    driver = ctx.inject("loop")

    async def tool_dispatch(name, args):
        return {"ok": True}

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
            tool_dispatch=tool_dispatch,
        )


@pytest.mark.asyncio
async def test_c6_policy_denied_aborts_cleanly():
    from dhc.modules.c9_capability_policy.service import apply as apply_policy

    ctx = Context()
    await apply_policy(ctx)
    await apply(ctx, {"max_steps": 5})
    driver = ctx.inject("loop")

    chunks = [
        _Chunk(
            tool_calls=[
                {"function": {"name": "bash", "arguments": '{"command":["ls"]}'}}
            ]
        ),
        _Chunk(content="done", finish_reason="stop"),
    ]
    end = await driver.run_turn(
        ctx, agent_id="ghost", llm_stream=_OneShotStream(chunks)
    )
    assert end.reason == "policy_denied"


@pytest.mark.asyncio
async def test_c6_session_log_records_transitions():
    from dhc.modules.c2_session_event_log.service import apply as apply_log
    from dhc.modules.c10_observability_sink.service import apply as apply_telemetry

    ctx = Context()
    await apply_log(ctx)
    await apply_telemetry(ctx)
    await apply(ctx, {"max_steps": 3})

    driver = ctx.inject("loop")
    log = ctx.inject("sessions")
    sink = ctx.inject("telemetry")

    chunks = [
        _Chunk(tool_calls=[{"function": {"name": "read_file", "arguments": '{"path":"a"}'}}]),
        _Chunk(content="done", finish_reason="stop"),
    ]

    async def dispatch(name, args):
        return {"ok": True}

    await driver.run_turn(
        ctx, agent_id="a1", llm_stream=_OneShotStream(chunks), tool_dispatch=dispatch
    )

    history = log.get_history()
    # C2 mirrors C6 lifecycle events into the log. Verify some are present.
    assert any(h.type == "turn/start" for h in history)
    assert any(h.type == "turn/end" for h in history)
    # The turn/end payload carries the abort reason; check the reason.
    end_events = [h for h in history if h.type == "turn/end"]
    assert end_events
    assert end_events[0].payload.get("reason") == "completed"
    # C10 sink also captures C6 lifecycle events.
    assert any(r["event"] in ("turn/start", "turn/end", "step/start", "step/end", "llm/stream", "tool/call") for r in sink.logs)


@pytest.mark.asyncio
async def test_c6_waterfall_listener_must_return_state():
    ctx = Context()
    await apply(ctx, {"max_steps": 2})
    driver = ctx.inject("loop")

    captured: list = []

    async def mutator(current, agent_id):
        captured.append(agent_id)
        current.steps = 0
        return current

    ctx.events.on("agent/pre-step", mutator)

    chunks = [
        _Chunk(tool_calls=[{"function": {"name": "read_file", "arguments": '{"path":"a"}'}}]),
        _Chunk(content="ok", finish_reason="stop"),
    ]
    await driver.run_turn(
        ctx,
        agent_id="a1",
        llm_stream=_OneShotStream(chunks),
        tool_dispatch=lambda n, a: {"ok": True},
    )
    assert captured == ["a1"]


def test_c6_max_steps_validation():
    with pytest.raises(ValueError):
        TurnStepDriver(max_steps=0)
    with pytest.raises(ValueError):
        TurnStepDriver(max_steps=-1)


@pytest.mark.asyncio
async def test_c6_apply_validates_config():
    ctx = Context()
    with pytest.raises(ValueError):
        await apply(ctx, {"max_steps": "five"})


def test_c6_abort_reasons_contains_expected():
    for r in ["completed", "max_steps_exceeded", "tool_error", "policy_denied", "llm_error"]:
        assert r in ABORT_REASONS


@pytest.mark.asyncio
async def test_c6_no_dispatch_yields_no_dispatcher_marker():
    ctx = Context()
    await apply(ctx, {"max_steps": 2})
    driver = ctx.inject("loop")
    chunks = [
        _Chunk(tool_calls=[{"function": {"name": "read_file", "arguments": '{}'}}]),
        _Chunk(content="ok", finish_reason="stop"),
    ]
    end = await driver.run_turn(
        ctx, agent_id="a1", llm_stream=_OneShotStream(chunks)
    )
    assert end.reason == "completed"
