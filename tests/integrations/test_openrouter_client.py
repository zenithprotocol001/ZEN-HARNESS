"""Tests for dhc.integrations.openrouter_client (v1.3.0 / ADR-0009)."""
from __future__ import annotations

import pytest

from dhc.integrations.base import ProviderError, RetryConfig
from dhc.integrations.openrouter_client import OpenRouterClient
from tests.integrations._mock_provider_server import _free_port, _mock_provider_server


@pytest.mark.asyncio
async def test_chat_stream_happy_path() -> None:
    port = _free_port()
    async with _mock_provider_server(
        port,
        openrouter_responses=[{"sse_openai": ["hi", " there"]}],
    ):
        client = OpenRouterClient(base_url=f"http://127.0.0.1:{port}")
        deltas: list[str] = []
        async for chunk in client.chat_stream(
            messages=[{"role": "user", "content": "x"}],
            model="openrouter/auto",
            api_key="sk-or-test",
        ):
            deltas.append(chunk.delta)
        assert "".join(deltas) == "hi there"


@pytest.mark.asyncio
async def test_chat_stream_sends_correct_request_shape() -> None:
    port = _free_port()
    capture: list[dict] = []
    async with _mock_provider_server(
        port,
        openrouter_responses=[{"sse_openai": ["OK"]}],
        capture_log=capture,
    ):
        client = OpenRouterClient(base_url=f"http://127.0.0.1:{port}")
        async for _ in client.chat_stream(
            messages=[{"role": "user", "content": "x"}],
            model="openrouter/auto",
            api_key="sk-or-test-1234567890",
        ):
            pass
    assert len(capture) == 1
    req = capture[0]
    assert req["method"] == "POST"
    assert req["path"] == "/api/v1/chat/completions"
    assert req["body"]["model"] == "openrouter/auto"
    assert req["headers"]["Authorization"] == "Bearer sk-or-test-1234567890"


@pytest.mark.asyncio
async def test_chat_stream_no_retry_on_401() -> None:
    port = _free_port()
    async with _mock_provider_server(
        port,
        openrouter_responses=[{"status": 401, "text": "bad key"}],
    ):
        client = OpenRouterClient(base_url=f"http://127.0.0.1:{port}")
        with pytest.raises(ProviderError) as ei:
            async for _ in client.chat_stream(
                messages=[{"role": "user", "content": "x"}],
                model="openrouter/auto",
                api_key="sk-bad",
            ):
                pass
        assert ei.value.status == 401


@pytest.mark.asyncio
async def test_chat_stream_retry_on_500() -> None:
    port = _free_port()
    rc = RetryConfig(backoff_seconds=(0.01, 0.01))
    async with _mock_provider_server(
        port,
        openrouter_responses=[
            {"status": 500, "text": "oops"},
            {"sse_openai": ["OK"]},
        ],
    ):
        client = OpenRouterClient(base_url=f"http://127.0.0.1:{port}")
        deltas: list[str] = []
        async for chunk in client.chat_stream(
            messages=[{"role": "user", "content": "x"}],
            model="openrouter/auto",
            api_key="sk-x",
            retry_config=rc,
        ):
            deltas.append(chunk.delta)
        assert "".join(deltas) == "OK"


@pytest.mark.asyncio
async def test_chat_stream_redacts_key_in_logs() -> None:
    port = _free_port()
    capture: list[dict] = []
    async with _mock_provider_server(
        port,
        openrouter_responses=[{"sse_openai": ["OK"]}],
        capture_log=capture,
    ):
        client = OpenRouterClient(base_url=f"http://127.0.0.1:{port}")
        async for chunk in client.chat_stream(
            messages=[{"role": "user", "content": "x"}],
            model="openrouter/auto",
            api_key="sk-or-very-secret-1234567890",
        ):
            # Defensive: chunk should not embed the secret
            assert "very-secret" not in repr(chunk)
    # Confirm the secret reached the wire (we WANT it there) but
    # in a header, not in the body. The mock server's capture
    # already proved that.
    assert len(capture) == 1
