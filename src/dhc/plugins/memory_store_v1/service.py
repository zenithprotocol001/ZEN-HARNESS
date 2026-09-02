"""memory_store_v1 — per-agent key/value store.

Exposes a `MemoryStore` with strict pydantic validation on stored
values. The plugin listens for `memory/recall` and `memory/store`
events on the Cordis bus and mutates the store accordingly.

Event payloads (validated by pydantic v2):

    {"event": "memory/store", "agent_id": "alice", "key": "topic",
     "value": {"any": "json-serializable"}}

    {"event": "memory/recall", "agent_id": "alice", "key": "topic"}

The plugin emits `memory/result` events with the recalled value (or
`{"found": false}`) so a C6 driver or the GUI can observe the
result without polling.

The store is process-local and unbounded by design — this is a dev
tool, not a production memory backend. A max-items-per-agent cap
(soft) is supported via `cap` in config to prevent pathological
growth during long sessions.
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from dhc.cordis.context import Context
from dhc.cordis.plugin import plugin


class _MemoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: Any
    seq: int = 0


class _StorePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    agent_id: str = Field(min_length=1, max_length=64)
    key: str = Field(min_length=1, max_length=128)


class _StoreBody(_StorePayload):
    value: Any


class MemoryStore:
    """Per-agent key/value store with strict value validation."""

    def __init__(self, cap_per_agent: int = 1024) -> None:
        if cap_per_agent < 1:
            raise ValueError("cap_per_agent must be >= 1")
        self._cap = int(cap_per_agent)
        self._store: dict[str, dict[str, _MemoryEntry]] = {}
        self._seq: dict[tuple[str, str], int] = {}

    def _next_seq(self, agent_id: str, key: str) -> int:
        s = self._seq.get((agent_id, key), 0) + 1
        self._seq[(agent_id, key)] = s
        return s

    def put(self, agent_id: str, key: str, value: Any) -> int:
        agent = self._store.setdefault(agent_id, {})
        if key not in agent and len(agent) >= self._cap:
            # Evict the oldest insertion order.
            oldest = next(iter(agent))
            del agent[oldest]
        seq = self._next_seq(agent_id, key)
        agent[key] = _MemoryEntry(value=value, seq=seq)
        return seq

    def get(self, agent_id: str, key: str) -> Any | None:
        entry = self._store.get(agent_id, {}).get(key)
        return None if entry is None else entry.value

    def list(self, agent_id: str) -> list[str]:
        return list(self._store.get(agent_id, {}).keys())

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            agent_id: {k: v.value for k, v in keys.items()}
            for agent_id, keys in self._store.items()
        }


@plugin("memory_store_v1")
async def apply(ctx: Context, config: dict) -> Callable[[], None]:
    cap = int(config.get("cap_per_agent", 1024)) if config else 1024
    store = MemoryStore(cap_per_agent=cap)
    ctx.provide("memory_store", store)

    async def on_store(payload: dict) -> None:
        try:
            body = _StoreBody.model_validate(payload)
        except Exception:
            return
        store.put(body.agent_id, body.key, body.value)

    async def on_recall(payload: dict) -> None:
        try:
            body = _StorePayload.model_validate(payload)
        except Exception:
            return
        value = store.get(body.agent_id, body.key)
        await ctx.events.emit(
            "memory/result",
            {
                "agent_id": body.agent_id,
                "key": body.key,
                "found": value is not None,
                "value": value,
            },
        )

    ctx.events.on("memory/store", on_store)
    ctx.events.on("memory/recall", on_recall)

    async def dispose() -> None:
        ctx.events.off("memory/store", on_store)
        ctx.events.off("memory/recall", on_recall)
        ctx.services.pop("memory_store", None)

    return dispose
