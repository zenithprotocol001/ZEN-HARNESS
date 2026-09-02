"""Tests for dhc.integrations.anthropic_client (v1.3.0 / ADR-0009)."""
from __future__ import annotations

import json

import pytest

from dhc.integrations.anthropic_client import AnthropicClient
from dhc.integrations.base import ProviderError, RetryConfig
from tests.integrations._mock_provider_server import _free_port, _mock_provider_server


@pytest.mark.asyncio
async def test_chat_stream_happy_path() -> None:
    port = _free_port()
    async with _mock_provider_server(
        port,
        anthropic_responses=[{"sse_anthropic": ["Hi", " there"]}],
    ):
        client = AnthropicClient(base_url=f"http://127.0.0.1:{port}")
        deltas: list[str] = []
        async for chunk in client.chat_stream(
            messages=[{"role": "user", "content": "Hi"}],
            model="anthropic/claude-3-5-sonnet-latest",
            api_key="sk-ant-test-1234567890",
        ):
            deltas.append(chunk.delta)
        assert "".join(deltas) == "Hi there"


@pytest.mark.asyncio
async def test_chat_stream_sends_correct_request_shape() -> None:
    port = _free_port()
    capture: list[dict] = []
    async with _mock_provider_server(
        port,
        anthropic_responses=[{"sse_anthropic": ["OK"]}],
        capture_log=capture,
    ):
        client = AnthropicClient(base_url=f"http://127.0.0.1:{port}")
        async for _ in client.chat_stream(
            messages=[{"role": "user", "content": "Hi"}],
            model="anthropic/claude-3-5-sonnet-latest",
            api_key="sk-ant-test-1234567890",
        ):
            pass
    assert len(capture) == 1
    req = capture[0]
    assert req["method"] == "POST"
    assert req["path"] == "/v1/messages"
    assert req["body"]["model"] == "anthropic/claude-3-5-sonnet-latest"
    assert req["body"]["stream"] is True
    assert req["body"]["max_tokens"] == 4096
    assert req["headers"]["x-api-key"] == "sk-ant-test-1234567890"
    assert req["headers"]["anthropic-version"] == "2023-06-01"


@pytest.mark.asyncio
async def test_chat_stream_uses_max_tokens_4096() -> None:
    port = _free_port()
    capture: list[dict] = []
    async with _mock_provider_server(
        port,
        anthropic_responses=[{"sse_anthropic": ["OK"]}],
        capture_log=capture,
    ):
        client = AnthropicClient(base_url=f"http://127.0.0.1:{port}")
        async for _ in client.chat_stream(
            messages=[{"role": "user", "content": "x"}],
            model="anthropic/claude-3-5-sonnet-latest",
            api_key="sk-ant-x",
        ):
            pass
    assert capture[0]["body"]["max_tokens"] == 4096


@pytest.mark.asyncio
async def test_chat_stream_no_retry_on_401() -> None:
    port = _free_port()
    async with _mock_provider_server(
        port,
        anthropic_responses=[{"status": 401, "text": "bad key"}],
    ):
        client = AnthropicClient(base_url=f"http://127.0.0.1:{port}")
        with pytest.raises(ProviderError) as ei:
            async for _ in client.chat_stream(
                messages=[{"role": "user", "content": "x"}],
                model="anthropic/claude-3-5-sonnet-latest",
                api_key="sk-bad",
            ):
                pass
        assert ei.value.status == 401


@pytest.mark.asyncio
async def test_chat_stream_no_retry_on_400() -> None:
    port = _free_port()
    async with _mock_provider_server(
        port,
        anthropic_responses=[{"status": 400, "text": "bad req"}],
    ):
        client = AnthropicClient(base_url=f"http://127.0.0.1:{port}")
        with pytest.raises(ProviderError) as ei:
            async for _ in client.chat_stream(
                messages=[{"role": "user", "content": "x"}],
                model="anthropic/claude-3-5-sonnet-latest",
                api_key="sk-x",
            ):
                pass
        assert ei.value.status == 400


@pytest.mark.asyncio
async def test_chat_stream_retry_on_529_overloaded() -> None:
    port = _free_port()
    rc = RetryConfig(backoff_seconds=(0.01, 0.01))
    async with _mock_provider_server(
        port,
        anthropic_responses=[
            {"status": 529, "text": "overloaded"},
            {"sse_anthropic": ["OK"]},
        ],
    ):
        client = AnthropicClient(base_url=f"http://127.0.0.1:{port}")
        deltas: list[str] = []
        async for chunk in client.chat_stream(
            messages=[{"role": "user", "content": "x"}],
            model="anthropic/claude-3-5-sonnet-latest",
            api_key="sk-x",
            retry_config=rc,
        ):
            deltas.append(chunk.delta)
        assert "".join(deltas) == "OK"


@pytest.mark.asyncio
async def test_chat_stream_retry_on_503() -> None:
    port = _free_port()
    rc = RetryConfig(backoff_seconds=(0.01, 0.01))
    async with _mock_provider_server(
        port,
        anthropic_responses=[
            {"status": 503, "text": "service down"},
            {"sse_anthropic": ["OK"]},
        ],
    ):
        client = AnthropicClient(base_url=f"http://127.0.0.1:{port}")
        deltas: list[str] = []
        async for chunk in client.chat_stream(
            messages=[{"role": "user", "content": "x"}],
            model="anthropic/claude-3-5-sonnet-latest",
            api_key="sk-x",
            retry_config=rc,
        ):
            deltas.append(chunk.delta)
        assert "".join(deltas) == "OK"


@pytest.mark.asyncio
async def test_chat_stream_gives_up_after_max_attempts() -> None:
    port = _free_port()
    rc = RetryConfig(backoff_seconds=(0.01, 0.01))
    async with _mock_provider_server(
        port,
        anthropic_responses=[
            {"status": 529},
            {"status": 529},
            {"status": 529},
        ],
    ):
        client = AnthropicClient(base_url=f"http://127.0.0.1:{port}")
        with pytest.raises(ProviderError, match="5xx after 3 attempts"):
            async for _ in client.chat_stream(
                messages=[{"role": "user", "content": "x"}],
                model="anthropic/claude-3-5-sonnet-latest",
                api_key="sk-x",
                retry_config=rc,
            ):
                pass


@pytest.mark.asyncio
async def test_chat_stream_propagates_tool_use() -> None:
    """A `content_block_start` with `type: tool_use` produces a
    StreamChunk with `tool_calls=[...]`."""
    port = _free_port()
    body = (
        b"event: message_start\n"
        b'data: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","content":[],"model":"claude-3-5-sonnet-latest","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":10,"output_tokens":0}}}\n\n'
        b"event: content_block_start\n"
        b'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_1","name":"get_weather","input":{}}}\n\n'
        b"event: content_block_delta\n"
        b'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"city\\":"}}\n\n'
        b"event: content_block_stop\n"
        b'data: {"type":"content_block_stop","index":0}\n\n'
        b"event: message_stop\n"
        b'data: {"type":"message_stop"}\n\n'
    )
    async with _mock_provider_server(
        port,
        anthropic_responses=[{"status": 200, "body": body, "headers": {"content-type": "text/event-stream"}}],
    ):
        client = AnthropicClient(base_url=f"http://127.0.0.1:{port}")
        chunks = []
        async for c in client.chat_stream(
            messages=[{"role": "user", "content": "x"}],
            model="anthropic/claude-3-5-sonnet-latest",
            api_key="sk-x",
        ):
            chunks.append(c)
        # At least one chunk should have tool_calls populated.
        assert any(c.tool_calls for c in chunks)


@pytest.mark.asyncio
async def test_chat_stream_extracts_stop_reason() -> None:
    port = _free_port()
    async with _mock_provider_server(
        port,
        anthropic_responses=[{"sse_anthropic": ["OK"]}],
    ):
        client = AnthropicClient(base_url=f"http://127.0.0.1:{port}")
        finish_reasons: list[str | None] = []
        async for c in client.chat_stream(
            messages=[{"role": "user", "content": "x"}],
            model="anthropic/claude-3-5-sonnet-latest",
            api_key="sk-x",
        ):
            finish_reasons.append(c.finish_reason)
        assert "end_turn" in finish_reasons
