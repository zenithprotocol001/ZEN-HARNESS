"""Mock LLM fixture for the v1.2.0 chat surface.

`start_mock_llm(host="127.0.0.1", port=3099, scenario="default")` runs
an aiohttp server that responds to the same SSE shape C7's
`chat_stream()` expects (and the older `stream()` shape, too).

The mock takes the user's last `user` or `system` message from the
request, picks a canned reply based on a `?scenario=` query
parameter (or the JSON body's `model` field), and streams the
reply as a sequence of small `data: {json}\n\n` chunks followed by
`data: [DONE]\n\n`.

Scenarios:

- `default`           — generic LLM-style reply
- `echo`              — echoes the user's last message verbatim
- `code`              — returns a Python code block
- `tool`              — emits one `tool_calls` delta then text
- `slow`              — sleeps between chunks (for streaming tests)
- `long`              — long multi-paragraph reply (for truncation tests)

The mock also exposes:

- `GET /healthz` — returns `{"ok": true}` so the C1 server can
  detect that the LLM is up.
- `POST /v1/chat/completions` — the chat surface
- `GET /v1/stream/{scenario}` — the legacy GET surface
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from aiohttp import web


def _pick_reply(body: dict[str, Any], scenario: str) -> list[dict[str, Any]]:
    """Return a list of `{delta, finish_reason?}` dicts that the
    mock will stream to the client. Each `delta` is a small chunk.
    """
    messages = body.get("messages") or []
    user_text = ""
    for m in messages:
        if m.get("role") == "user":
            user_text = str(m.get("content") or "")
    if not user_text:
        user_text = "Hello"

    if scenario == "echo":
        reply = user_text
    elif scenario == "code":
        reply = (
            "Here is a Python snippet:\n\n"
            "```python\n"
            "def hello(name: str) -> str:\n"
            "    return f'Hello, {name}!'\n"
            "```\n\n"
            "Run it and you'll see the greeting."
        )
    elif scenario == "tool":
        reply = "I will use a tool to find that out."
    elif scenario == "long":
        reply = (
            "This is a deliberately long reply used to test the "
            "truncation and message-size invariants. " * 50
        )
    else:  # default
        reply = (
            f"You said: {user_text!r}. The mock LLM acknowledges "
            "your message and returns this canned response."
        )

    # Chunk the reply into ~16-character pieces so the streaming
    # surface is exercised.
    chunks: list[dict[str, Any]] = []
    for i in range(0, len(reply), 16):
        chunks.append({"delta": reply[i : i + 16]})
    if scenario == "tool":
        # Prepend a tool-call chunk.
        chunks.insert(
            0,
            {
                "delta": "",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "web_search", "arguments": "{\"q\": \"dhc\"}"},
                    }
                ],
            },
        )
    chunks.append({"delta": "", "finish_reason": "stop"})
    return chunks


async def _stream_chunks(request: web.Request, scenario: str) -> web.StreamResponse:
    if request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not scenario or scenario == "default":
            scenario = str(body.get("model") or "default")
    else:
        body = {}
        if not scenario:
            scenario = "default"

    chunks = _pick_reply(body, scenario)
    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)
    base_index = 0
    for ch in chunks:
        delta = ch.get("delta", "")
        if scenario == "slow":
            await asyncio.sleep(0.02)
        if ch.get("tool_calls"):
            payload = {
                "id": "chatcmpl-mock",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "tool_calls": ch["tool_calls"]},
                    }
                ],
            }
        else:
            payload = {
                "id": "chatcmpl-mock",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": delta},
                        "finish_reason": ch.get("finish_reason"),
                    }
                ],
            }
        line = f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
        await response.write(line.encode("utf-8"))
        base_index += 1
    await response.write(b"data: [DONE]\n\n")
    await response.write_eof()
    return response


async def _chat_completions(request: web.Request) -> web.StreamResponse:
    scenario = request.query.get("scenario", "default")
    return await _stream_chunks(request, scenario)


async def _legacy_stream(request: web.Request) -> web.StreamResponse:
    scenario = request.match_info.get("scenario", "default")
    return await _stream_chunks(request, scenario)


async def _healthz(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/healthz", _healthz)
    app.router.add_post("/v1/chat/completions", _chat_completions)
    app.router.add_get("/v1/stream/{scenario}", _legacy_stream)
    return app


def start_mock_llm(host: str = "127.0.0.1", port: int = 3099) -> web.AppRunner:
    """Construct (but do not start) an aiohttp runner for the mock.
    The caller is responsible for `await runner.setup()` and
    `await site.start()`. The harness `scripts/start_mock_llm.ps1`
    helper wires those up.
    """
    runner = web.AppRunner(build_app())
    return runner


if __name__ == "__main__":  # pragma: no cover - manual launch only
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=3099)
    args = p.parse_args()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    runner = start_mock_llm(host=args.host, port=args.port)
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, args.host, args.port)
    loop.run_until_complete(site.start())
    print(f"mock LLM listening on http://{args.host}:{args.port}")
    try:
        loop.run_forever()
    finally:
        loop.run_until_complete(runner.cleanup())


__all__ = ["build_app", "start_mock_llm"]
