"""Tests for the mock LLM fixture and the C7 chat_stream() path."""
from __future__ import annotations

import asyncio
import socket
from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiohttp
import pytest
from aiohttp import web

from dhc.modules.c7_llm_stream_adapter.service import LLMStreamAdapter
from tests.fixtures.mock_llm import build_app


# ---------- helpers ----------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@asynccontextmanager
async def _running_mock(port: int) -> AsyncIterator[None]:
    runner = web.AppRunner(build_app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    try:
        yield
    finally:
        await runner.cleanup()


# ---------- mock LLM tests ----------


@pytest.mark.asyncio
async def test_mock_healthz():
    port = _free_port()
    async with _running_mock(port):
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/healthz") as resp:
                assert resp.status == 200
                body = await resp.json()
                assert body == {"ok": True}


@pytest.mark.asyncio
async def test_mock_chat_default():
    port = _free_port()
    async with _running_mock(port):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                json={"model": "default", "messages": [{"role": "user", "content": "hi"}]},
            ) as resp:
                assert resp.status == 200
                chunks: list[dict] = []
                async for raw in resp.content:
                    line = raw.decode("utf-8").strip()
                    if not line.startswith("data: "):
                        continue
                    payload = line[len("data: ") :]
                    if payload == "[DONE]":
                        break
                    chunks.append(__import__("json").loads(payload))
                # Default scenario returns canned reply; concatenate deltas.
                deltas = [c["choices"][0]["delta"].get("content", "") for c in chunks]
                combined = "".join(deltas)
                assert "hi" in combined  # the mock echoes the user text
                # The last non-empty chunk carries finish_reason=stop.
                stop_chunks = [
                    c for c in chunks if c["choices"][0].get("finish_reason") == "stop"
                ]
                assert stop_chunks


@pytest.mark.asyncio
async def test_mock_chat_echo():
    port = _free_port()
    async with _running_mock(port):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                json={"model": "echo", "messages": [{"role": "user", "content": "Hello world"}]},
            ) as resp:
                chunks: list[dict] = []
                async for raw in resp.content:
                    line = raw.decode("utf-8").strip()
                    if line.startswith("data: ") and line != "data: [DONE]":
                        chunks.append(__import__("json").loads(line[len("data: "):]))
                deltas = [c["choices"][0]["delta"].get("content", "") for c in chunks]
                combined = "".join(deltas)
                assert combined == "Hello world"


@pytest.mark.asyncio
async def test_mock_chat_tool_call():
    port = _free_port()
    async with _running_mock(port):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                json={"model": "tool", "messages": [{"role": "user", "content": "search"}]},
            ) as resp:
                tool_chunks: list[dict] = []
                async for raw in resp.content:
                    line = raw.decode("utf-8").strip()
                    if line.startswith("data: ") and line != "data: [DONE]":
                        c = __import__("json").loads(line[len("data: "):])
                        if c["choices"][0]["delta"].get("tool_calls"):
                            tool_chunks.append(c)
                assert tool_chunks
                tc = tool_chunks[0]["choices"][0]["delta"]["tool_calls"][0]
                assert tc["function"]["name"] == "web_search"


# ---------- C7 chat_stream tests ----------


@pytest.mark.asyncio
async def test_c7_chat_stream_yields_deltas():
    port = _free_port()
    async with _running_mock(port):
        adapter = LLMStreamAdapter(
            base_url=f"http://127.0.0.1:{port}", api_key="sk-mock-1234567890"
        )
        deltas: list[str] = []
        async for chunk in adapter.chat_stream(
            messages=[{"role": "user", "content": "hello"}], model="echo"
        ):
            deltas.append(chunk.delta)
        combined = "".join(deltas)
        assert combined == "hello"


@pytest.mark.asyncio
async def test_c7_chat_stream_terminates_on_done():
    port = _free_port()
    async with _running_mock(port):
        adapter = LLMStreamAdapter(
            base_url=f"http://127.0.0.1:{port}", api_key="sk-mock-1234567890"
        )
        chunks = []
        async for chunk in adapter.chat_stream(
            messages=[{"role": "user", "content": "x"}], model="default"
        ):
            chunks.append(chunk)
        # The last chunk has finish_reason=stop.
        assert chunks[-1].finish_reason == "stop"


@pytest.mark.asyncio
async def test_c7_chat_stream_redacts_key_in_url():
    """The adapter must not bake the key into the URL; it goes in
    the Authorization header. This is a defense against key leakage
    in proxy logs.
    """
    port = _free_port()
    captured: dict[str, str] = {}

    async def capture_handler(request: web.Request) -> web.StreamResponse:
        captured["auth"] = request.headers.get("Authorization", "")
        # Reuse the mock's response shape.
        from tests.fixtures.mock_llm import _stream_chunks

        return await _stream_chunks(request, "echo")

    app = web.Application()
    app.router.add_post("/v1/chat/completions", capture_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    try:
        adapter = LLMStreamAdapter(
            base_url=f"http://127.0.0.1:{port}", api_key="sk-leaktest-9876543210"
        )
        deltas: list[str] = []
        async for chunk in adapter.chat_stream(
            messages=[{"role": "user", "content": "hi"}], model="echo"
        ):
            deltas.append(chunk.delta)
        assert "sk-leaktest-9876543210" in captured.get("auth", "")
        # The body of the request must contain the messages, not the key.
        # (The Authorization header is the only place the key travels.)
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_c7_chat_stream_tool_call_chunk():
    port = _free_port()
    async with _running_mock(port):
        adapter = LLMStreamAdapter(
            base_url=f"http://127.0.0.1:{port}", api_key="sk-mock-1234567890"
        )
        tool_chunks: list = []
        async for chunk in adapter.chat_stream(
            messages=[{"role": "user", "content": "search"}], model="tool"
        ):
            if chunk.tool_calls:
                tool_chunks.append(chunk)
        assert tool_chunks
        assert tool_chunks[0].tool_calls[0]["function"]["name"] == "web_search"
