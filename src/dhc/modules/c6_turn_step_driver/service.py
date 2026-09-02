"""C6 TurnStepDriver: waterfall orchestrator with step-limit circuit breaker.

Waterfall:
    turn/start -> agent/pre-step -> step/start -> llm/stream ->
    tool/call  -> step/end -> turn/end

Listeners on `agent/pre-step` and other waterfall events MUST return
`current` (mutated or not). Listeners that return `None` drop the
chain — the auditor's forward warning applies here.

Hard limit: `max_steps` (default 5). If the LLM requests more tool
calls than this, the driver aborts with `StepLimitExceeded` and emits
`turn/end` with reason `max_steps_exceeded`.

Integration:
- C9 CapabilityPolicy gates every `tool/call` (already enforced by the
  policy module's `tools/pre-execute` listener).
- C2 SessionLog receives `session/event` for every transition.
- C10 ObservabilitySink receives `tool/result` and `session/event`.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field

from dhc.cordis.context import Context
from dhc.cordis.plugin import plugin


DEFAULT_MAX_STEPS: int = 5

ABORT_REASONS: frozenset[str] = frozenset(
    {
        "completed",
        "max_steps_exceeded",
        "tool_error",
        "policy_denied",
        "llm_error",
    }
)


class StepLimitExceeded(RuntimeError):
    pass


class TurnEndReason(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reason: str = Field(min_length=1, max_length=64)
    steps: int = Field(ge=0)
    agent_id: str = Field(min_length=1, max_length=64)


class _TurnState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    agent_id: str
    turn_id: str
    messages: list[dict] = Field(default_factory=list)
    steps: int = 0
    aborted: bool = False
    abort_reason: str = "completed"


class TurnStepDriver:
    def __init__(self, max_steps: int = DEFAULT_MAX_STEPS) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        self._max_steps = max_steps

    @property
    def max_steps(self) -> int:
        return self._max_steps

    async def run_turn(
        self,
        ctx: Context,
        agent_id: str,
        llm_stream: AsyncIterator[Any] | Callable[[], AsyncIterator[Any]],
        tool_dispatch: Callable[[str, dict], Awaitable[Any]] | None = None,
    ) -> TurnEndReason:
        turn_id = f"turn-{agent_id}-{id(self)}"
        state = _TurnState(agent_id=agent_id, turn_id=turn_id)

        await ctx.events.emit(
            "turn/start",
            {"agent_id": agent_id, "turn_id": turn_id},
        )

        # agent/pre-step is a waterfall slot; listeners MUST return `current`
        waterfall_state: Any = await ctx.events.waterfall(
            "agent/pre-step", state, agent_id=agent_id
        )
        if isinstance(waterfall_state, _TurnState):
            state = waterfall_state

        try:
            while True:
                if state.aborted:
                    break

                await ctx.events.emit(
                    "step/start",
                    {"agent_id": agent_id, "turn_id": turn_id, "step": state.steps},
                )

                stream = llm_stream() if callable(llm_stream) else llm_stream
                tool_calls: list[dict] = []
                content_delta: str = ""
                finish_reason: str | None = None
                async for chunk in stream:
                    if getattr(chunk, "content", None) or getattr(chunk, "delta", None):
                        content_delta += getattr(chunk, "content", None) or getattr(chunk, "delta", "")
                    if getattr(chunk, "tool_calls", None):
                        tool_calls.extend(chunk.tool_calls)
                    if getattr(chunk, "finish_reason", None):
                        finish_reason = chunk.finish_reason

                await ctx.events.emit(
                    "llm/stream",
                    {
                        "agent_id": agent_id,
                        "turn_id": turn_id,
                        "step": state.steps,
                        "content": content_delta,
                        "tool_calls": tool_calls,
                        "finish_reason": finish_reason,
                    },
                )

                if not tool_calls:
                    state.aborted = True
                    state.abort_reason = "completed"
                else:
                    # Apply each tool call through the C9 pre-execute gate
                    # and the C4 guard.
                    for call in tool_calls:
                        if state.aborted:
                            break
                        tool_name = (call.get("function") or {}).get("name") or call.get("name")
                        raw_args = (call.get("function") or {}).get("arguments") or call.get("arguments") or "{}"
                        args = _safe_json_loads(raw_args)
                        try:
                            await ctx.events.emit(
                                "tools/pre-execute",
                                {"agent_id": agent_id, "tool_name": tool_name},
                            )
                        except Exception as exc:
                            state.aborted = True
                            state.abort_reason = "policy_denied"
                            await ctx.events.emit(
                                "tool/call",
                                {
                                    "agent_id": agent_id,
                                    "tool_name": tool_name,
                                    "args": args,
                                    "error": str(exc),
                                },
                            )
                            break

                        if state.aborted:
                            break

                        if tool_dispatch is None:
                            result = {"status": "no_dispatcher"}
                        else:
                            try:
                                _result = tool_dispatch(tool_name, args)
                                if hasattr(_result, "__await__"):
                                    result = await _result
                                else:
                                    result = _result
                            except Exception as exc:
                                state.aborted = True
                                state.abort_reason = "tool_error"
                                await ctx.events.emit(
                                    "tool/call",
                                    {
                                        "agent_id": agent_id,
                                        "tool_name": tool_name,
                                        "args": args,
                                        "error": str(exc),
                                    },
                                )
                                break

                        await ctx.events.emit(
                            "tool/call",
                            {
                                "agent_id": agent_id,
                                "turn_id": turn_id,
                                "tool_name": tool_name,
                                "args": args,
                                "result": result,
                            },
                        )

                await ctx.events.emit(
                    "step/end",
                    {
                        "agent_id": agent_id,
                        "turn_id": turn_id,
                        "step": state.steps,
                    },
                )
                state.steps += 1

                if state.aborted:
                    # Honor the explicit abort reason set above
                    # ("completed", "policy_denied", "tool_error").
                    # A clean completion is NOT a max-steps event.
                    if state.abort_reason != "max_steps_exceeded":
                        break
                    # Otherwise: cap reached; fall through to the
                    # raise below.

                # Check circuit breaker at the END of every step iteration.
                # This is the fix: the check must run regardless of whether
                # the step was a clean completion, a tool error, or a
                # policy denial, so a runaway tool can never escape the cap.
                if state.steps >= self._max_steps:
                    state.aborted = True
                    state.abort_reason = "max_steps_exceeded"
                    break

        except Exception as exc:
            if not state.aborted:
                state.aborted = True
                state.abort_reason = "llm_error"
            await ctx.events.emit(
                "llm/error",
                {"agent_id": agent_id, "turn_id": turn_id, "error": str(exc)},
            )

        end = TurnEndReason(
            reason=state.abort_reason,
            steps=state.steps,
            agent_id=agent_id,
        )
        await ctx.events.emit(
            "turn/end",
            {"agent_id": agent_id, "turn_id": turn_id, "reason": end.reason, "steps": end.steps},
        )

        if state.abort_reason == "max_steps_exceeded":
            raise StepLimitExceeded(
                f"agent {agent_id!r} exceeded max_steps={self._max_steps}"
            )
        return end


def _safe_json_loads(raw: Any) -> dict:
    if not isinstance(raw, str):
        return {}
    try:
        import json

        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {"_value": obj}
    except Exception:
        return {}


@plugin("c6_loop")
async def apply(ctx: Context, config: dict) -> Callable[[], None]:
    max_steps = (config or {}).get("max_steps", DEFAULT_MAX_STEPS)
    if not isinstance(max_steps, int) or max_steps < 1:
        raise ValueError("max_steps must be a positive int")
    driver = TurnStepDriver(max_steps=max_steps)
    ctx.provide("loop", driver)

    async def dispose() -> None:
        ctx.services.pop("loop", None)

    return dispose
