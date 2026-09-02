"""Deterministic aiohttp mock LLM SSE server.

`GET /v1/stream?scenario={happy|infinite|fragmented}` emits scripted SSE bytes
matching the fixtures in `scripts.py`. The server has no clocks; all data is
precomputed.
"""

from __future__ import annotations

from typing import AsyncIterator

from aiohttp import web

from dhc.fixtures.mock_llm.scripts import (
    FRAGMENTED_CHUNKS,
    make_happy_bytes,
    make_infinite_bytes,
)


def _chunked(data: bytes, chunk_size: int) -> AsyncIterator[bytes]:
    for i in range(0, len(data), chunk_size):
        yield data[i : i + chunk_size]


async def _stream_happy(_request: web.Request) -> web.StreamResponse:
    resp = web.StreamResponse(
        status=200,
        headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"},
    )
    await resp.prepare(_request)
    for chunk in _chunked(make_happy_bytes(), 16):
        await resp.write(chunk)
    await resp.write_eof()
    return resp


async def _stream_infinite(_request: web.Request) -> web.StreamResponse:
    resp = web.StreamResponse(
        status=200,
        headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"},
    )
    await resp.prepare(_request)
    payload = make_infinite_bytes()
    for _ in range(64):
        await resp.write(payload)
        await resp.write(b": keepalive\n\n")
    await resp.write_eof()
    return resp


async def _stream_fragmented(_request: web.Request) -> web.StreamResponse:
    resp = web.StreamResponse(
        status=200,
        headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"},
    )
    await resp.prepare(_request)
    for chunk in FRAGMENTED_CHUNKS:
        await resp.write(chunk)
        await resp.write(b": ping\n\n")
    await resp.write_eof()
    return resp


async def _healthz(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/healthz", _healthz)
    app.router.add_get("/v1/stream/happy", _stream_happy)
    app.router.add_get("/v1/stream/infinite", _stream_infinite)
    app.router.add_get("/v1/stream/fragmented", _stream_fragmented)
    return app


class MockLLMServer:
    def __init__(self) -> None:
        self.app = create_app()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self.port: int = 0

    async def start(self) -> int:
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await self._site.start()
        sockets = self._runner.sites[0]._server.sockets  # type: ignore[attr-defined]
        self.port = sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
        if self._runner is not None:
            await self._runner.cleanup()
