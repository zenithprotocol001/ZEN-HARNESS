"""Hand-rolled mock HTTP server for v1.3.0 provider client tests.

Reuses the v1.2.x pattern (see `tests/fixtures/mock_llm.py`):
aiohttp.web on an ephemeral port. Returns canned SSE responses
for the OpenAI, Anthropic, and OpenRouter shapes.

Usage in a test:

    async with _mock_provider_server(port, responses=[...]) as url:
        client = OpenAIClient()
        async for chunk in client.chat_stream(...):
            ...

The `responses` list is a queue: each request to the server pops
the next response. If the queue is empty, the server returns 500.
This lets a test assert "first call 503, second call 200" by
passing `responses=[{"status": 503}, {"status": 200, "body": "..."}]`.
"""
from __future__ import annotations

import asyncio
import json
import socket
from contextlib import asynccontextmanager
from typing import AsyncIterator

from aiohttp import web


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_openai_sse(*deltas: str, include_usage: bool = True) -> bytes:
    """Build an OpenAI-shape SSE response with the given text deltas.

    When `include_usage` is True (default for v1.3.1 tests), the
    response ends with a choices-less chunk carrying a `usage`
    block, then `data: [DONE]`. This is the shape the live
    OpenAI endpoint emits when the request body contains
    `stream_options.include_usage`.
    """
    out = b""
    for d in deltas:
        chunk = {
            "id": "chatcmpl-1",
            "object": "chat.completion.chunk",
            "choices": [{
                "index": 0,
                "delta": {"content": d},
                "finish_reason": None,
            }],
        }
        out += b"data: " + json.dumps(chunk).encode("utf-8") + b"\n\n"
    if include_usage:
        usage_chunk = {
            "id": "chatcmpl-1",
            "object": "chat.completion.chunk",
            "choices": [],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 7,
                "total_tokens": 19,
            },
        }
        out += b"data: " + json.dumps(usage_chunk).encode("utf-8") + b"\n\n"
    out += b"data: [DONE]\n\n"
    return out


def _build_anthropic_sse(*text_deltas: str, include_usage: bool = True) -> bytes:
    """Build an Anthropic-shape SSE response with the given text deltas.

    When `include_usage` is True (default for v1.3.1 tests), the
    `message_delta` event includes a `usage` block.
    """
    out = b""
    out += b"event: message_start\n"
    out += b'data: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","content":[],"model":"claude-3-5-sonnet-latest","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":10,"output_tokens":0}}}\n\n'
    out += b"event: content_block_start\n"
    out += b'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
    for d in text_deltas:
        out += b"event: content_block_delta\n"
        out += b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":' + json.dumps(d).encode("utf-8") + b"}}\n\n"
    out += b"event: content_block_stop\n"
    out += b'data: {"type":"content_block_stop","index":0}\n\n'
    out += b"event: message_delta\n"
    if include_usage:
        out += (
            b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},'
            b'"usage":{"input_tokens":10,"output_tokens":3}}\n\n'
        )
    else:
        out += b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null}}\n\n'
    out += b"event: message_stop\n"
    out += b'data: {"type":"message_stop"}\n\n'
    return out


@asynccontextmanager
async def _mock_provider_server(
    port: int,
    *,
    openai_responses: list[dict] | None = None,
    anthropic_responses: list[dict] | None = None,
    openrouter_responses: list[dict] | None = None,
    capture_log: list[dict] | None = None,
) -> AsyncIterator[str]:
    """Start a mock provider server on `port` and yield its base URL.

    `*_responses` is a queue (FIFO) of canned responses. Each
    response is a dict:
      - `{"status": int, "body": bytes, "headers": dict}`
      - `{"status": 503}` — empty 503
      - `{"sse_openai": ["hi", " there"]}` — OpenAI SSE with deltas
      - `{"sse_anthropic": ["hi", " there"]}` — Anthropic SSE

    If a queue is exhausted, the server returns 500.
    """
    openai_q = list(openai_responses or [])
    anthropic_q = list(anthropic_responses or [])
    openrouter_q = list(openrouter_responses or [])

    async def _drain(q: list[dict]) -> web.Response:
        if not q:
            return web.Response(status=500, text="no more responses")
        spec = q.pop(0)
        if "sse_openai" in spec:
            body = _build_openai_sse(*spec["sse_openai"])
            return web.Response(
                status=spec.get("status", 200),
                body=body,
                content_type="text/event-stream",
                headers=spec.get("headers", {}),
            )
        if "sse_anthropic" in spec:
            body = _build_anthropic_sse(*spec["sse_anthropic"])
            return web.Response(
                status=spec.get("status", 200),
                body=body,
                content_type="text/event-stream",
                headers=spec.get("headers", {}),
            )
        if "status" in spec and "body" not in spec:
            return web.Response(status=spec["status"], text=spec.get("text", ""))
        return web.Response(
            status=spec.get("status", 200),
            body=spec.get("body", b""),
            headers=spec.get("headers", {}),
        )

    async def _record(request: web.Request) -> None:
        if capture_log is not None:
            try:
                body = await request.json()
            except Exception:
                body = None
            capture_log.append({
                "method": request.method,
                "path": request.path,
                "headers": dict(request.headers),
                "body": body,
            })

    async def openai_handler(request: web.Request) -> web.Response:
        await _record(request)
        return await _drain(openai_q)

    async def anthropic_handler(request: web.Request) -> web.Response:
        await _record(request)
        return await _drain(anthropic_q)

    async def openrouter_handler(request: web.Request) -> web.Response:
        await _record(request)
        return await _drain(openrouter_q)

    app = web.Application()
    app.router.add_post("/v1/chat/completions", openai_handler)
    app.router.add_post("/v1/messages", anthropic_handler)
    app.router.add_post("/api/v1/chat/completions", openrouter_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()
