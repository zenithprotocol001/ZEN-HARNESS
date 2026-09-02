import pytest

from dhc.modules.c7_llm_stream_adapter.service import (
    LLMStreamAdapter,
    _MAX_BUFFER,
)
from dhc.modules.c7_llm_stream_adapter.test_helpers import fragmented_bytes


@pytest.mark.asyncio
async def test_c7_buffer_overflow_raises_and_does_not_leak_key():
    key = "sk-abcdefghij1234567890"
    adapter = LLMStreamAdapter(base_url="http://x", api_key=key)
    # Send many small chunks that cumulatively exceed _MAX_BUFFER.
    # Each chunk is partial (no \n\n terminator) so the buffer cannot
    # drain and grows monotonically. This tests the streaming buffer
    # check on fragmented input, not a single big payload.
    chunk_count = 64
    chunk_size = (_MAX_BUFFER // chunk_count) + 1
    chunks = [b"x" * chunk_size for _ in range(chunk_count)]
    with pytest.raises(Exception) as excinfo:
        async for _ in adapter._consume_sse(fragmented_bytes(chunks)):
            pass
    msg = str(excinfo.value)
    assert key not in msg
    assert "sk-a***890" in msg or "***" in msg


@pytest.mark.asyncio
async def test_c7_fragmented_overflow_boundary():
    """Send many small chunks that exactly overshoot the cap; the overflow
    must trigger on the boundary, not on the first chunk, and must still
    redact the key."""
    key = "sk-abcdefghij1234567890"
    adapter = LLMStreamAdapter(base_url="http://x", api_key=key)
    small = b"a" * 1024
    chunks = [small] * 2048
    with pytest.raises(Exception) as excinfo:
        async for _ in adapter._consume_sse(fragmented_bytes(chunks)):
            pass
    assert key not in str(excinfo.value)


@pytest.mark.asyncio
async def test_c7_malformed_fragmented_json_does_not_crash():
    adapter = LLMStreamAdapter(base_url="http://x", api_key="sk-abcdefghij1234567890")
    payload = (
        b'data: {"choices":[{"delta":{"con'
        b'tent":"hello"'
        b'}}]}\n\n'
        b'data: {"choices":[{"delta":{"con'
        b'tent":" world"'
        b'}}]}\n\ndata: [DONE]\n\n'
    )
    out: list = []
    async for c in adapter._consume_sse(fragmented_bytes([payload[:5], payload[5:17], payload[17:33], payload[33:]])):
        out.append(c)
    assert any(c.delta == "hello" for c in out)
    assert any(c.delta == " world" for c in out)
