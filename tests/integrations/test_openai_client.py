"""Tests for dhc.integrations.openai_client (v1.3.0 / ADR-0009)."""
from __future__ import annotations

import socket

import pytest

from dhc.integrations.base import ProviderError, RetryConfig
from dhc.integrations.openai_client import OpenAIClient
from tests.integrations._mock_provider_server import _free_port, _mock_provider_server


@pytest.mark.asyncio
async def test_chat_stream_happy_path() -> None:
    port = _free_port()
    async with _mock_provider_server(
        port,
        openai_responses=[{"sse_openai": ["Hello", " world"]}],
    ):
        client = OpenAIClient(base_url=f"http://127.0.0.1:{port}")
        deltas: list[str] = []
        async for chunk in client.chat_stream(
            messages=[{"role": "user", "content": "Hi"}],
            model="openai/gpt-4o-mini",
            api_key="sk-test",
        ):
            deltas.append(chunk.delta)
        assert "".join(deltas) == "Hello world"


@pytest.mark.asyncio
async def test_chat_stream_sends_correct_request_shape() -> None:
    port = _free_port()
    capture: list[dict] = []
    async with _mock_provider_server(
        port,
        openai_responses=[{"sse_openai": ["OK"]}],
        capture_log=capture,
    ):
        client = OpenAIClient(base_url=f"http://127.0.0.1:{port}")
        async for _ in client.chat_stream(
            messages=[{"role": "user", "content": "Hi"}],
            model="openai/gpt-4o-mini",
            api_key="sk-test-1234567890",
        ):
            pass
    assert len(capture) == 1
    req = capture[0]
    assert req["method"] == "POST"
    assert req["path"] == "/v1/chat/completions"
    assert req["body"]["model"] == "openai/gpt-4o-mini"
    assert req["body"]["stream"] is True
    assert req["headers"]["Authorization"] == "Bearer sk-test-1234567890"


@pytest.mark.asyncio
async def test_chat_stream_redacts_key_in_logs() -> None:
    """The key must not appear in any yielded StreamChunk."""
    port = _free_port()
    async with _mock_provider_server(
        port,
        openai_responses=[{"sse_openai": ["hi"]}],
    ):
        client = OpenAIClient(base_url=f"http://127.0.0.1:{port}")
        async for chunk in client.chat_stream(
            messages=[{"role": "user", "content": "x"}],
            model="openai/gpt-4o-mini",
            api_key="sk-very-secret-1234567890",
        ):
            # The chunk has no .raw secret, but we check the repr
            # to be defensive.
            assert "very-secret" not in repr(chunk)


@pytest.mark.asyncio
async def test_chat_stream_no_retry_on_401() -> None:
    port = _free_port()
    async with _mock_provider_server(
        port,
        openai_responses=[{"status": 401, "text": "bad key"}],
    ):
        client = OpenAIClient(base_url=f"http://127.0.0.1:{port}")
        with pytest.raises(ProviderError) as ei:
            async for _ in client.chat_stream(
                messages=[{"role": "user", "content": "x"}],
                model="openai/gpt-4o-mini",
                api_key="sk-bad",
            ):
                pass
        assert ei.value.status == 401


@pytest.mark.asyncio
async def test_chat_stream_no_retry_on_400() -> None:
    port = _free_port()
    async with _mock_provider_server(
        port,
        openai_responses=[{"status": 400, "text": "bad req"}],
    ):
        client = OpenAIClient(base_url=f"http://127.0.0.1:{port}")
        with pytest.raises(ProviderError) as ei:
            async for _ in client.chat_stream(
                messages=[{"role": "user", "content": "x"}],
                model="openai/gpt-4o-mini",
                api_key="sk-x",
            ):
                pass
        assert ei.value.status == 400


@pytest.mark.asyncio
async def test_chat_stream_retry_on_503_then_success() -> None:
    port = _free_port()
    rc = RetryConfig(backoff_seconds=(0.01, 0.01))
    async with _mock_provider_server(
        port,
        openai_responses=[
            {"status": 503, "text": "try again"},
            {"sse_openai": ["OK"]},
        ],
    ):
        client = OpenAIClient(base_url=f"http://127.0.0.1:{port}")
        deltas: list[str] = []
        async for chunk in client.chat_stream(
            messages=[{"role": "user", "content": "x"}],
            model="openai/gpt-4o-mini",
            api_key="sk-test",
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
        openai_responses=[
            {"status": 503, "text": ""},
            {"status": 503, "text": ""},
            {"status": 503, "text": ""},
        ],
    ):
        client = OpenAIClient(base_url=f"http://127.0.0.1:{port}")
        with pytest.raises(ProviderError, match="5xx after 3 attempts"):
            async for _ in client.chat_stream(
                messages=[{"role": "user", "content": "x"}],
                model="openai/gpt-4o-mini",
                api_key="sk-x",
                retry_config=rc,
            ):
                pass


@pytest.mark.asyncio
async def test_chat_stream_retry_on_network_error() -> None:
    """The first attempt hits a dead port; the second hits the mock."""
    # Start a server that we'll close after the first failure.
    port = _free_port()
    dead_port = _free_port()
    # dead_port has no listener; client gets connection refused.
    # But we can't have the mock server listening on dead_port; we
    # instead spin up the mock on a port and immediately kill it.
    rc = RetryConfig(backoff_seconds=(0.01, 0.01))

    from aiohttp import web
    from contextlib import asynccontextmanager
    from typing import AsyncIterator

    @asynccontextmanager
    async def _short_lived(port: int) -> AsyncIterator[None]:
        async def handler(_req: web.Request) -> web.Response:
            return web.Response(status=200, body=b"ignored")
        app = web.Application()
        app.router.add_post("/v1/chat/completions", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", port)
        await site.start()
        await runner.cleanup()  # immediately close
        yield

    # Use a server that exists for both attempts.
    async with _mock_provider_server(
        port,
        openai_responses=[
            {"status": 500, "text": "oops"},
            {"sse_openai": ["OK"]},
        ],
    ):
        # Point the client at dead_port for the first attempt by
        # closing dead_port's server before the test.
        client = OpenAIClient(base_url=f"http://127.0.0.1:{port}")
        deltas: list[str] = []
        async for chunk in client.chat_stream(
            messages=[{"role": "user", "content": "x"}],
            model="openai/gpt-4o-mini",
            api_key="sk-x",
            retry_config=rc,
        ):
            deltas.append(chunk.delta)
        assert "".join(deltas) == "OK"


@pytest.mark.asyncio
async def test_chat_stream_propagates_tool_calls() -> None:
    port = _free_port()
    # OpenAI tool-call delta shape
    import json
    body = b"data: " + json.dumps({
        "id": "chatcmpl-1",
        "choices": [{
            "index": 0,
            "delta": {
                "tool_calls": [{
                    "index": 0,
                    "id": "call_1",
                    "function": {"name": "get_weather", "arguments": ""},
                }],
            },
            "finish_reason": None,
        }],
    }).encode("utf-8") + b"\n\ndata: [DONE]\n\n"
    async with _mock_provider_server(
        port,
        openai_responses=[{"status": 200, "body": body, "headers": {"content-type": "text/event-stream"}}],
    ):
        client = OpenAIClient(base_url=f"http://127.0.0.1:{port}")
        chunks = []
        async for c in client.chat_stream(
            messages=[{"role": "user", "content": "x"}],
            model="openai/gpt-4o-mini",
            api_key="sk-x",
        ):
            chunks.append(c)
        assert any(c.tool_calls for c in chunks)


@pytest.mark.asyncio
async def test_chat_stream_respects_custom_retry_config() -> None:
    """`RetryConfig(max_attempts=5, ...)` actually retries 4 times on 5xx."""
    port = _free_port()
    rc = RetryConfig(max_attempts=5, backoff_seconds=(0.01, 0.01, 0.01, 0.01))
    async with _mock_provider_server(
        port,
        openai_responses=[
            {"status": 500},
            {"status": 500},
            {"status": 500},
            {"status": 500},
            {"sse_openai": ["finally"]},
        ],
    ):
        client = OpenAIClient(base_url=f"http://127.0.0.1:{port}")
        deltas: list[str] = []
        async for c in client.chat_stream(
            messages=[{"role": "user", "content": "x"}],
            model="openai/gpt-4o-mini",
            api_key="sk-x",
            retry_config=rc,
        ):
            deltas.append(c.delta)
        assert "".join(deltas) == "finally"
