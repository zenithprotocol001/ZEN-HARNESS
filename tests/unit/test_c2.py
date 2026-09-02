import pytest
from pydantic import ValidationError

from dhc.cordis.context import Context
from dhc.modules.c2_session_event_log.service import SessionEvent, apply


@pytest.mark.asyncio
async def test_c2_append_only():
    ctx = Context()
    await apply(ctx)
    log = ctx.inject("sessions")

    evt = SessionEvent(id="1", type="test", payload={"a": 1})
    log.append(evt)

    history = log.get_history()
    assert len(history) == 1
    assert history[0].id == "1"
    assert history[0].type == "test"
    assert history[0].payload == {"a": 1}


@pytest.mark.asyncio
async def test_c2_branching():
    ctx = Context()
    await apply(ctx)
    log = ctx.inject("sessions")

    log.append(SessionEvent(id="1", type="init"))
    log.append(SessionEvent(id="2", type="step"))
    log.branch("checkpoint-a")

    log.append(SessionEvent(id="3", type="final"))

    main_history = log.get_history()
    branched = log.get_branch("checkpoint-a")

    assert len(main_history) == 3
    assert len(branched) == 2
    assert [e.id for e in branched] == ["1", "2"]


@pytest.mark.asyncio
async def test_c2_event_emission_routes_to_log():
    ctx = Context()
    await apply(ctx)
    log = ctx.inject("sessions")

    await ctx.events.emit("session/event", {"id": "evt-1", "type": "from-emit", "payload": {"x": 1}})

    history = log.get_history()
    assert len(history) == 1
    assert history[0].id == "evt-1"
    assert history[0].payload == {"x": 1}


@pytest.mark.asyncio
async def test_c2_event_is_frozen():
    evt = SessionEvent(id="1", type="x")
    with pytest.raises(ValidationError):
        evt.id = "2"
