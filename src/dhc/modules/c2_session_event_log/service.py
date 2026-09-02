"""C2 SessionEventLog: append-only event log with branching and strict no-mutation.

The session log has two distinct surfaces:
- `get_history()` returns the immutable, append-only record of all
  `session/event` emissions. This is the canonical source of truth
  for the turn lifecycle and must never be bounded or evicted.
- `get_heartbeats()` returns a bounded ring buffer of the most recent
  `system/heartbeat` events. Heartbeats are an out-of-band telemetry
  signal (liveness, liveness-of-the-runtime) and must NOT be appended
  to the immutable session history, otherwise an infinite heartbeat
  source will OOM the process and corrupt the canonical record.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Deque, List, Tuple

from pydantic import BaseModel, ConfigDict, Field

from dhc.cordis.context import Context
from dhc.cordis.plugin import plugin


DEFAULT_HEARTBEAT_RING_SIZE: int = 256


class SessionEvent(BaseModel):
    """Immutable record of a single session-scoped event.

    `model_config.frozen=True` blocks attribute mutation on a per-event basis.
    The list/tuple surface in `SessionLog` enforces append-only at the
    collection level.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    type: str
    payload: dict = Field(default_factory=dict)


class HeartbeatEvent(BaseModel):
    """A single heartbeat record (liveness telemetry).

    Distinct from `SessionEvent` so the heartbeat ring buffer cannot
    be confused with the immutable session log.
    """

    model_config = ConfigDict(frozen=True)

    seq: int
    ts_ms: int
    source: str
    detail: dict = Field(default_factory=dict)


class SessionLog:
    def __init__(self, heartbeat_ring_size: int = DEFAULT_HEARTBEAT_RING_SIZE) -> None:
        self._events: List[SessionEvent] = []
        self._branches: dict[str, list[SessionEvent]] = {}
        self._heartbeats: Deque[HeartbeatEvent] = deque(maxlen=heartbeat_ring_size)
        self._heartbeat_seq: int = 0

    def append(self, event: SessionEvent) -> None:
        self._events.append(event)

    def get_history(self) -> Tuple[SessionEvent, ...]:
        """Return an immutable tuple snapshot of the current log.

        Returning a tuple is the primary mutation defense: tuples have no
        `append`, `pop`, `__setitem__`, or `__delitem__` surface. Any such
        attempt raises `AttributeError`.
        """
        return tuple(self._events)

    def branch(self, branch_id: str) -> str:
        self._branches[branch_id] = list(self._events)
        return branch_id

    def get_branch(self, branch_id: str) -> Tuple[SessionEvent, ...]:
        return tuple(self._branches.get(branch_id, ()))

    def record_heartbeat(self, ts_ms: int, source: str, detail: dict) -> HeartbeatEvent:
        """Record a heartbeat in the bounded ring buffer.

        This MUST NOT be appended to the immutable session log. Doing so
        would let an infinite heartbeat emitter cause unbounded memory
        growth and corrupt the canonical record.
        """
        self._heartbeat_seq += 1
        ev = HeartbeatEvent(
            seq=self._heartbeat_seq,
            ts_ms=ts_ms,
            source=source,
            detail=detail,
        )
        self._heartbeats.append(ev)
        return ev

    def get_heartbeats(self) -> Tuple[HeartbeatEvent, ...]:
        return tuple(self._heartbeats)

    def __len__(self) -> int:
        return len(self._events)


@plugin("c2_session")
async def apply(ctx: Context, config: dict) -> Any:
    ring_size = int((config or {}).get("heartbeat_ring_size", DEFAULT_HEARTBEAT_RING_SIZE))
    log = SessionLog(heartbeat_ring_size=ring_size)
    ctx.provide("sessions", log)

    async def on_session_event(event_data: dict) -> None:
        log.append(SessionEvent(**event_data))

    async def on_heartbeat(payload: dict) -> None:
        ts_ms = int(payload.get("ts_ms") or 0)
        source = str(payload.get("source") or "unknown")
        detail = dict(payload.get("detail") or {})
        log.record_heartbeat(ts_ms=ts_ms, source=source, detail=detail)

    def make_lifecycle_listener(event_name: str):
        async def on_lifecycle(*args, **kwargs) -> None:
            if not args:
                return
            payload = args[0]
            if not isinstance(payload, dict):
                return
            ev_type = (
                payload.get("event_name")
                or payload.get("type")
                or event_name
            )
            ev_id = (
                payload.get("id")
                or f"{ev_type}-{payload.get('turn_id', '')}-{payload.get('step', '')}"
            )
            log.append(SessionEvent(id=str(ev_id), type=str(ev_type), payload=payload))
        return on_lifecycle

    # Register for the canonical event name AND for the C6 lifecycle
    # event names so a session log subscriber sees the full timeline.
    registered: list[tuple[str, Any]] = []
    ctx.events.on("session/event", on_session_event)
    for ev in (
        "turn/start",
        "agent/pre-step",
        "step/start",
        "llm/stream",
        "tool/call",
        "step/end",
        "turn/end",
        "system/error",
    ):
        listener = make_lifecycle_listener(ev)
        ctx.events.on(ev, listener)
        registered.append((ev, listener))
    ctx.events.on("system/heartbeat", on_heartbeat)

    async def dispose() -> None:
        ctx.events.off("session/event", on_session_event)
        for ev, listener in registered:
            ctx.events.off(ev, listener)
        ctx.events.off("system/heartbeat", on_heartbeat)

    return dispose
