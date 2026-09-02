import pytest

from dhc.cordis.context import Context
from dhc.modules.c10_observability_sink.service import (
    ObservabilitySink,
    apply,
    scrub_pii,
)


@pytest.mark.asyncio
async def test_c10_scrubbing_openai_key():
    ctx = Context()
    await apply(ctx)
    sink = ctx.inject("telemetry")

    sink.log_event("test", {"key": "sk-12345678901234567890", "user": "test@example.com"})

    assert len(sink.logs) == 1
    assert sink.logs[0]["payload"]["key"] == "***REDACTED***"
    assert sink.logs[0]["payload"]["user"] == "***REDACTED***"


@pytest.mark.asyncio
async def test_c10_scrubbing_recursive():
    nested = {
        "outer": {
            "inner": ["contact me at john.doe@example.com", "safe text"],
            "token": "sk_live_abcdefghijklmnop1234",
        },
        "list_field": [{"email": "x@y.z"}],
    }
    scrubbed = scrub_pii(nested)
    assert scrubbed["outer"]["inner"][0] == "contact me at ***REDACTED***"
    assert scrubbed["outer"]["inner"][1] == "safe text"
    assert scrubbed["outer"]["token"] == "***REDACTED***"
    assert scrubbed["list_field"][0]["email"] == "***REDACTED***"


def test_c10_scrubbing_passthrough_for_scalars():
    assert scrub_pii(42) == 42
    assert scrub_pii(None) is None
    assert scrub_pii(3.14) == 3.14


@pytest.mark.asyncio
async def test_c10_event_bus_routes_to_sink():
    ctx = Context()
    await apply(ctx)
    sink = ctx.inject("telemetry")

    await ctx.events.emit("tool/result", {"output": "all good"})
    await ctx.events.emit("session/event", {"id": "1", "type": "t"})

    events = [r["event"] for r in sink.logs]
    assert "tool/result" in events
    assert "session/event" in events


@pytest.mark.asyncio
async def test_c10_sink_records_unchanged_payload_for_safe_data():
    sink = ObservabilitySink()
    sink.log_event("ping", {"ok": True, "n": 7})
    assert sink.logs[0]["payload"] == {"ok": True, "n": 7}
