"""C10 ObservabilitySink: PII/secret scrubbing + structured event capture.

The scrubber is recursive across dict/list/scalar, runs before any payload is
recorded, and uses fixed patterns for common secret shapes (OpenAI/Stripe
API keys, email addresses). The sink is the canonical "no silent failures"
target: any module that catches an exception must route through it.
"""

import re
from typing import Any

from dhc.cordis.context import Context
from dhc.cordis.plugin import plugin

PII_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"sk_live_[a-zA-Z0-9]{16,}"),
    re.compile(r"ghp_[a-zA-Z0-9]{20,}"),
    re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"),
]

REDACTION = "***REDACTED***"


def scrub_pii(data: Any) -> Any:
    if isinstance(data, str):
        for pattern in PII_PATTERNS:
            data = pattern.sub(REDACTION, data)
        return data
    if isinstance(data, dict):
        return {k: scrub_pii(v) for k, v in data.items()}
    if isinstance(data, list):
        return [scrub_pii(v) for v in data]
    if isinstance(data, tuple):
        return tuple(scrub_pii(v) for v in data)
    return data


class ObservabilitySink:
    def __init__(self) -> None:
        self.logs: list[dict[str, Any]] = []

    def log_event(self, event_name: str, payload: Any) -> dict[str, Any]:
        scrubbed = scrub_pii(payload)
        record = {"event": event_name, "payload": scrubbed}
        self.logs.append(record)
        return record

    def clear(self) -> None:
        self.logs.clear()


@plugin("c10_telemetry")
async def apply(ctx: Context, config: dict) -> Any:
    sink = ObservabilitySink()
    ctx.provide("telemetry", sink)

    async def on_tool_result(payload: Any) -> None:
        sink.log_event("tool/result", payload)

    async def on_session_event(payload: Any) -> None:
        sink.log_event("session/event", payload)

    def make_lifecycle_logger(event_name: str):
        async def on_lifecycle(*args, **kwargs) -> None:
            # Accept both emit (single payload) and waterfall
            # (current, *args, **kwargs) call shapes.
            if args and isinstance(args[0], dict):
                sink.log_event(event_name, args[0])
            elif args:
                sink.log_event(event_name, {"value": args[0]})
            else:
                sink.log_event(event_name, dict(kwargs))
        return on_lifecycle

    ctx.events.on("tool/result", on_tool_result)
    ctx.events.on("session/event", on_session_event)
    lifecycle_loggers = []
    for ev in (
        "turn/start",
        "agent/pre-step",
        "step/start",
        "llm/stream",
        "tool/call",
        "step/end",
        "turn/end",
        "system/error",
        "system/heartbeat",
    ):
        listener = make_lifecycle_logger(ev)
        ctx.events.on(ev, listener)
        lifecycle_loggers.append((ev, listener))

    async def dispose() -> None:
        ctx.events.off("tool/result", on_tool_result)
        ctx.events.off("session/event", on_session_event)
        for ev, listener in lifecycle_loggers:
            ctx.events.off(ev, listener)

    return dispose
