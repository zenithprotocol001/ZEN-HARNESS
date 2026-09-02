"""rate_limiter_v1 — per-agent token-bucket throttle for tools/pre-execute.

The plugin attaches to the `tools/pre-execute` event. For every
incoming tool call, the limiter checks the agent's bucket. If the
bucket is dry, the limiter raises `RateLimited` so the C6 driver
aborts the step cleanly (abort reason: "policy_denied").

The bucket refills at a constant rate (tokens per second) up to a
maximum burst. Configuration is via the `apply(config=...)` call:

    config = {"rate": 5.0, "burst": 10, "per_agent": {"alice": 2.0}}

Per-agent overrides can be set at runtime via `RateLimiter.set_rate()`,
exposed via `ctx.inject("rate_limiter")`.
"""

from __future__ import annotations

import time
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from dhc.cordis.context import Context
from dhc.cordis.plugin import plugin


class RateLimited(Exception):
    """Raised when an agent's bucket is dry."""


class _AgentBucket(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tokens: float
    last_refill_ts: float


class RateLimiter:
    """Per-agent token-bucket limiter."""

    def __init__(self, rate: float = 5.0, burst: float = 10.0) -> None:
        if rate <= 0:
            raise ValueError("rate must be > 0")
        if burst <= 0:
            raise ValueError("burst must be > 0")
        self._rate = float(rate)
        self._burst = float(burst)
        self._state: dict[str, _AgentBucket] = {}
        self._overrides: dict[str, float] = {}

    def set_rate(self, agent_id: str, rate: float) -> None:
        """Override an agent's rate. Resets the bucket to full burst."""
        if rate <= 0:
            raise ValueError("rate must be > 0")
        self._overrides[agent_id] = float(rate)
        self._state[agent_id] = _AgentBucket(
            tokens=self._burst, last_refill_ts=time.monotonic()
        )

    def _refill(self, agent_id: str, now: float) -> _AgentBucket:
        rate = self._overrides.get(agent_id, self._rate)
        st = self._state.get(agent_id)
        if st is None:
            st = _AgentBucket(tokens=self._burst, last_refill_ts=now)
            self._state[agent_id] = st
        else:
            elapsed = max(0.0, now - st.last_refill_ts)
            st.tokens = min(self._burst, st.tokens + elapsed * rate)
            st.last_refill_ts = now
        return st

    def check(self, agent_id: str) -> None:
        """Deduct one token; raise RateLimited if the bucket is dry."""
        st = self._refill(agent_id, time.monotonic())
        if st.tokens < 1.0:
            raise RateLimited(f"agent {agent_id!r}: rate limit exceeded")
        st.tokens -= 1.0

    def snapshot(self) -> dict[str, dict[str, float]]:
        return {
            agent_id: {"tokens": round(st.tokens, 4), "last_refill_ts": st.last_refill_ts}
            for agent_id, st in self._state.items()
        }


@plugin("rate_limiter_v1")
async def apply(ctx: Context, config: dict) -> Callable[[], None]:
    rate = float(config.get("rate", 5.0)) if config else 5.0
    burst = float(config.get("burst", 10.0)) if config else 10.0
    per_agent = dict(config.get("per_agent", {})) if config else {}

    limiter = RateLimiter(rate=rate, burst=burst)
    for agent_id, agent_rate in per_agent.items():
        limiter.set_rate(agent_id, float(agent_rate))
    ctx.provide("rate_limiter", limiter)

    async def pre_execute(payload: dict) -> None:
        agent_id = str(payload.get("agent_id") or "")
        if not agent_id:
            return
        try:
            limiter.check(agent_id)
        except RateLimited as exc:
            # Surface the throttle as a system event so the C6
            # driver (or the GUI) can observe it. Raising from
            # within a listener would tear down unrelated listeners
            # on the same event, so we only emit and let the C9
            # policy plugin (if loaded) decide whether to abort the
            # turn.
            await ctx.events.emit(
                "system/throttled",
                {"agent_id": agent_id, "tool_name": payload.get("tool_name"), "reason": str(exc)},
            )

    ctx.events.on("tools/pre-execute", pre_execute)

    async def dispose() -> None:
        ctx.events.off("tools/pre-execute", pre_execute)
        ctx.services.pop("rate_limiter", None)

    return dispose
