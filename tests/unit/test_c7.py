import pytest

from dhc.cordis.context import Context
from dhc.fixtures.mock_llm.scripts import (
    FRAGMENTED_CHUNKS,
    make_happy_bytes,
    make_infinite_bytes,
)
from dhc.modules.c7_llm_stream_adapter.service import LLMStreamAdapter
from dhc.modules.c7_llm_stream_adapter.test_helpers import fragmented_bytes


@pytest.mark.asyncio
async def test_c7_consumes_well_formed_sse():
    adapter = LLMStreamAdapter(base_url="http://x", api_key="sk-abcdefghij1234567890")
    chunks: list = []
    async for c in adapter._consume_sse(fragmented_bytes([make_happy_bytes()])):
        chunks.append(c)
    assert any(c.delta == "The README contains installation instructions." for c in chunks)
    assert any(c.finish_reason == "stop" for c in chunks)
    assert any(c.tool_calls for c in chunks)


@pytest.mark.asyncio
async def test_c7_reassembles_fragmented_chunks():
    adapter = LLMStreamAdapter(base_url="http://x", api_key="sk-abcdefghij1234567890")
    out: list = []
    async for c in adapter._consume_sse(fragmented_bytes(FRAGMENTED_CHUNKS)):
        out.append(c)
    assert len(out) >= 4
    assert out[-1].finish_reason == "stop"


@pytest.mark.asyncio
async def test_c7_handles_garbage_chunks_without_crashing():
    adapter = LLMStreamAdapter(base_url="http://x", api_key="sk-abcdefghij1234567890")
    garbage = [b"\x00\x01", b"this is not sse", b"\n", b"data: {not json}\n\n", b"\n\n"]
    out: list = []
    async for c in adapter._consume_sse(fragmented_bytes(garbage)):
        out.append(c)
    assert out == []


@pytest.mark.asyncio
async def test_c7_drops_sse_comments_and_heartbeats():
    adapter = LLMStreamAdapter(base_url="http://x", api_key="sk-abcdefghij1234567890")
    payload = b": keepalive\n\ndata: {\"choices\":[{\"delta\":{\"content\":\"ok\"}}]}\n\n"
    out: list = []
    async for c in adapter._consume_sse(fragmented_bytes([payload])):
        out.append(c)
    assert [c.delta for c in out] == ["ok"]


@pytest.mark.asyncio
async def test_c7_emits_done_and_stops():
    adapter = LLMStreamAdapter(base_url="http://x", api_key="sk-abcdefghij1234567890")
    payload = b'data: {"choices":[{"delta":{"content":"x"}}]}\n\ndata: [DONE]\n\n'
    seen: list = []
    async for c in adapter._consume_sse(fragmented_bytes([payload])):
        seen.append(c)
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_c7_redacts_api_key():
    a = LLMStreamAdapter(base_url="http://x", api_key="sk-abcdefghij1234567890")
    assert "sk-abcdefghij1234567890" not in a.redacted_key
    assert "***" in a.redacted_key


@pytest.mark.asyncio
async def test_c7_apply_provides_service():
    from dhc.modules.c7_llm_stream_adapter.service import apply
    ctx = Context()
    await apply(ctx, {"base_url": "http://h", "api_key": "sk-abcdefghij1234567890"})
    assert ctx.inject("llm") is not None
    assert ctx.inject("llm").redacted_key.startswith("sk-")
