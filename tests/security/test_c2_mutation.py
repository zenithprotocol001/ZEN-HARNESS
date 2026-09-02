import pytest

from dhc.cordis.context import Context
from dhc.modules.c2_session_event_log.service import SessionEvent, apply


@pytest.mark.asyncio
async def test_c2_mutation_attack():
    ctx = Context()
    await apply(ctx)
    log = ctx.inject("sessions")

    log.append(SessionEvent(id="1", type="test"))
    history = log.get_history()

    with pytest.raises(AttributeError):
        history.append(SessionEvent(id="2", type="hack"))

    with pytest.raises(AttributeError):
        history.pop()


@pytest.mark.asyncio
async def test_c2_no_mutation_via_list_cast():
    """A determined attacker casts back to list; the internal list must still be intact."""
    ctx = Context()
    await apply(ctx)
    log = ctx.inject("sessions")

    log.append(SessionEvent(id="1", type="ok"))
    snapshot = log.get_history()

    try:
        as_list = list(snapshot)
        as_list.pop()
    except Exception:
        pass

    assert len(log.get_history()) == 1


@pytest.mark.asyncio
async def test_c2_frozen_event_blocks_attribute_attack():
    ctx = Context()
    await apply(ctx)
    log = ctx.inject("sessions")

    log.append(SessionEvent(id="1", type="ok"))
    evt = log.get_history()[0]

    with pytest.raises(Exception):
        evt.id = "tampered"
