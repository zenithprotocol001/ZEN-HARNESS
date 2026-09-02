"""Tests for v1.2.0 chat, session, and secret HTTP routes on C1.

These tests run a real aiohttp test server backed by a real
GuiWebCore with `sessions_dir` and `secrets_dir` set to tmp paths.
The chat WS is exercised against a real mock LLM aiohttp server
in the same process (started on an ephemeral port).
"""
from __future__ import annotations

import asyncio
import json
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from dhc.cordis.context import Context
from dhc.cordis.secrets import SecretsService
from dhc.modules.c1_gui_web_core.service import GuiWebCore
from dhc.modules.c7_llm_stream_adapter.service import LLMStreamAdapter
from dhc.services.session_manager import SessionManager
from tests.fixtures.mock_llm import build_app as build_mock_app


# ---------- helpers ----------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@asynccontextmanager
async def _running_mock(port: int) -> AsyncIterator[str]:
    runner = web.AppRunner(build_mock_app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


@asynccontextmanager
async def _running_c1(tmp_path: Path, llm_port: int) -> AsyncIterator[tuple[TestClient, str, str]]:
    """Bring up a GuiWebCore wired to a SessionManager, SecretsService,
    and an LLMStreamAdapter pointing at the mock.

    Yields (test_client, token, base_url). The test_client already has
    the loopback origin set, so all requests are allowed.
    """
    sessions_dir = tmp_path / "sessions"
    secrets_dir = tmp_path / "secrets"
    sessions_dir.mkdir()
    secrets_dir.mkdir()
    sm = SessionManager(sessions_dir)
    ss = SecretsService(secrets_dir)
    gwc = GuiWebCore(
        host="127.0.0.1",
        port=0,
        require_token=True,
        sessions_dir=sessions_dir,
        secrets_dir=secrets_dir,
    )
    # Replace the auto-built services with the ones we built (so the
    # tests share state with the routes' lookups).
    gwc.session_manager = sm
    gwc.secrets_service = ss
    gwc.app["ctx"] = Context()
    # Provide an LLM adapter pointing at the mock.
    adapter = LLMStreamAdapter(
        base_url=f"http://127.0.0.1:{llm_port}", api_key="sk-mock-1234567890"
    )
    gwc.app["ctx"].provide("llm", adapter)
    gwc.app["ctx"].provide("csp", "test-csp")
    server = TestServer(gwc.app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield client, gwc.token, f"{server.host}:{server.port}"
    finally:
        await client.close()


# ---------- sessions ----------


@pytest.mark.asyncio
async def test_sessions_create_list_get(tmp_path: Path):
    mock_port = _free_port()
    async with _running_mock(mock_port), _running_c1(tmp_path, mock_port) as (client, token, _):
        # Create a session (no auth required for /api/sessions in this
        # v1.2.0 surface, per the v1.1.0 contract for /api/eval).
        r = await client.post("/api/sessions", json={"title": "first"})
        assert r.status == 201
        s = await r.json()
        sid = s["id"]
        assert s["title"] == "first"
        # List contains it.
        r = await client.get("/api/sessions")
        assert r.status == 200
        body = await r.json()
        ids = [x["id"] for x in body["sessions"]]
        assert sid in ids
        # Get the session.
        r = await client.get(f"/api/sessions/{sid}")
        assert r.status == 200
        s2 = await r.json()
        assert s2["id"] == sid
        assert s2["title"] == "first"


@pytest.mark.asyncio
async def test_sessions_patch_and_soft_delete(tmp_path: Path):
    mock_port = _free_port()
    async with _running_mock(mock_port), _running_c1(tmp_path, mock_port) as (client, token, _):
        r = await client.post("/api/sessions", json={})
        s = await r.json()
        sid = s["id"]
        # Patch title + pinned + tags + model.
        r = await client.patch(
            f"/api/sessions/{sid}",
            json={"title": "new", "pinned": True, "tags": ["x"], "model": "m"},
        )
        assert r.status == 200
        s2 = await r.json()
        assert s2["title"] == "new"
        assert s2["pinned"] is True
        assert s2["tags"] == ["x"]
        assert s2["model"] == "m"
        # Soft delete.
        r = await client.delete(f"/api/sessions/{sid}")
        assert r.status == 204
        # Default list excludes archived.
        r = await client.get("/api/sessions")
        body = await r.json()
        assert all(s["id"] != sid for s in body["sessions"])
        # include_archived surfaces it.
        r = await client.get("/api/sessions?archived=1")
        body = await r.json()
        assert any(s["id"] == sid for s in body["sessions"])


@pytest.mark.asyncio
async def test_sessions_hard_delete(tmp_path: Path):
    mock_port = _free_port()
    async with _running_mock(mock_port), _running_c1(tmp_path, mock_port) as (client, token, _):
        r = await client.post("/api/sessions", json={})
        s = await r.json()
        sid = s["id"]
        r = await client.delete(f"/api/sessions/{sid}?hard=1")
        assert r.status == 204
        r = await client.get(f"/api/sessions/{sid}")
        assert r.status == 404


@pytest.mark.asyncio
async def test_sessions_post_message_round_trip(tmp_path: Path):
    mock_port = _free_port()
    async with _running_mock(mock_port), _running_c1(tmp_path, mock_port) as (client, token, _):
        r = await client.post("/api/sessions", json={})
        s = await r.json()
        sid = s["id"]
        # Use the mock LLM's "echo" model so we can assert the reply.
        r = await client.post(
            f"/api/sessions/{sid}/messages",
            json={"content": "hello world", "model": "echo"},
        )
        assert r.status == 201
        body = await r.json()
        assert body["user_message"]["role"] == "user"
        assert body["user_message"]["content"] == "hello world"
        assert body["assistant_message"]["role"] == "assistant"
        assert body["assistant_message"]["content"] == "hello world"
        # And the session log has 2 messages.
        r = await client.get(f"/api/sessions/{sid}")
        s2 = await r.json()
        assert len(s2["messages"]) == 2


@pytest.mark.asyncio
async def test_sessions_get_missing(tmp_path: Path):
    mock_port = _free_port()
    async with _running_mock(mock_port), _running_c1(tmp_path, mock_port) as (client, token, _):
        r = await client.get("/api/sessions/s_does_not_exist")
        assert r.status == 404


@pytest.mark.asyncio
async def test_sessions_search(tmp_path: Path):
    mock_port = _free_port()
    async with _running_mock(mock_port), _running_c1(tmp_path, mock_port) as (client, token, _):
        r = await client.post("/api/sessions", json={"title": "Plugin debugging"})
        sid_a = (await r.json())["id"]
        r = await client.post("/api/sessions", json={"title": "C8 webhook"})
        sid_b = (await r.json())["id"]
        r = await client.post(f"/api/sessions/{sid_b}/messages", json={"content": "harness architecture"})
        r = await client.get("/api/sessions?search=plugin")
        body = await r.json()
        assert any(s["id"] == sid_a for s in body["sessions"])


@pytest.mark.asyncio
async def test_sessions_q_alias_full_text_search(tmp_path: Path):
    """The v1.2.1 ``?q=`` alias calls SessionManager.search() and
    matches both title and message content (case-insensitive).
    """
    mock_port = _free_port()
    async with _running_mock(mock_port), _running_c1(tmp_path, mock_port) as (client, token, _):
        r = await client.post("/api/sessions", json={"title": "Python Architecture"})
        sid_py = (await r.json())["id"]
        r = await client.post("/api/sessions", json={"title": "JavaScript Tips"})
        sid_js = (await r.json())["id"]
        r = await client.post("/api/sessions", json={"title": "Standup"})
        sid_st = (await r.json())["id"]
        # post a message with content that does not appear in any title
        r = await client.post(
            f"/api/sessions/{sid_st}/messages",
            json={"content": "Reviewing the async event loop today."},
        )
        assert r.status == 201

        # title match: only sid_py
        r = await client.get("/api/sessions?q=python")
        body = await r.json()
        ids = [s["id"] for s in body["sessions"]]
        assert ids == [sid_py]

        # content match: only sid_st
        r = await client.get("/api/sessions?q=async")
        body = await r.json()
        ids = [s["id"] for s in body["sessions"]]
        assert ids == [sid_st]

        # case-insensitive
        r = await client.get("/api/sessions?q=ASYNC")
        body = await r.json()
        assert [s["id"] for s in body["sessions"]] == [sid_st]

        # no match: empty
        r = await client.get("/api/sessions?q=zzz-no-match")
        body = await r.json()
        assert body["sessions"] == []

        # empty q: empty (not a full listing — callers use no q for that)
        r = await client.get("/api/sessions?q=")
        body = await r.json()
        assert body["sessions"] == []


# ---------- secrets ----------


@pytest.mark.asyncio
async def test_secrets_list_put_get_delete(tmp_path: Path):
    mock_port = _free_port()
    async with _running_mock(mock_port), _running_c1(tmp_path, mock_port) as (client, token, _):
        # Empty.
        r = await client.get("/api/secrets")
        assert r.status == 200
        body = await r.json()
        assert body["names"] == []
        # Put.
        r = await client.put("/api/secrets/openai_test", json={"value": "sk-12345"})
        assert r.status == 204
        # List shows the name.
        r = await client.get("/api/secrets")
        body = await r.json()
        assert body["names"] == ["openai_test"]
        # The list endpoint never returns values.
        assert "sk-12345" not in str(body)
        # Delete.
        r = await client.delete("/api/secrets/openai_test")
        assert r.status == 204
        r = await client.get("/api/secrets")
        body = await r.json()
        assert body["names"] == []


@pytest.mark.asyncio
async def test_secrets_put_validation(tmp_path: Path):
    mock_port = _free_port()
    async with _running_mock(mock_port), _running_c1(tmp_path, mock_port) as (client, token, _):
        # Missing 'value' → 400.
        r = await client.put("/api/secrets/openai", json={})
        assert r.status == 400
        # Non-string 'value' → 400.
        r = await client.put("/api/secrets/openai", json={"value": 12345})
        assert r.status == 400
        # Empty name at the URL level is a 404 (the route requires
        # a name segment). The service-layer empty-name check is
        # covered in tests/chat/test_secrets.py.
        r = await client.put("/api/secrets/openai", json={"value": "ok"})
        assert r.status == 204


@pytest.mark.asyncio
async def test_secrets_delete_missing(tmp_path: Path):
    mock_port = _free_port()
    async with _running_mock(mock_port), _running_c1(tmp_path, mock_port) as (client, token, _):
        r = await client.delete("/api/secrets/does_not_exist")
        assert r.status == 404


# ---------- chat WS ----------


@pytest.mark.asyncio
async def test_ws_chat_send_echo(tmp_path: Path):
    mock_port = _free_port()
    async with _running_mock(mock_port), _running_c1(tmp_path, mock_port) as (client, token, base):
        # Create a session pinned to the echo model so the mock
        # echoes the user text verbatim.
        r = await client.post("/api/sessions", json={"title": "echo-test"})
        s = await r.json()
        sid = s["id"]
        await client.patch(f"/api/sessions/{sid}", json={"model": "echo"})
        # Connect to /ws/chat with the token. Set Origin so the
        # loopback origin check passes.
        ws_url = f"ws://{base}/ws/chat?token={token}"
        async with client.session.ws_connect(ws_url, origin="http://127.0.0.1") as ws:
            await ws.send_str(json.dumps({"type": "chat.send", "session_id": sid, "text": "hello"}))
            deltas: list[str] = []
            done_seen = False
            for _ in range(50):
                msg = await asyncio.wait_for(ws.receive(), timeout=5.0)
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                payload = json.loads(msg.data)
                if payload.get("type") == "chat.delta":
                    deltas.append(payload["delta"])
                elif payload.get("type") == "chat.done":
                    done_seen = True
                    break
                elif payload.get("type") == "chat.error":
                    pytest.fail(f"chat.error: {payload}")
        assert "".join(deltas) == "hello"
        assert done_seen


@pytest.mark.asyncio
async def test_ws_chat_rejects_wrong_token(tmp_path: Path):
    mock_port = _free_port()
    async with _running_mock(mock_port), _running_c1(tmp_path, mock_port) as (client, token, base):
        ws_url = f"ws://{base}/ws/chat?token=wrong"
        # The connection should be refused (401) at the HTTP layer.
        with pytest.raises(aiohttp.WSServerHandshakeError):
            async with client.session.ws_connect(ws_url, origin="http://127.0.0.1") as ws:
                pass


@pytest.mark.asyncio
async def test_ws_chat_unknown_type(tmp_path: Path):
    mock_port = _free_port()
    async with _running_mock(mock_port), _running_c1(tmp_path, mock_port) as (client, token, base):
        r = await client.post("/api/sessions", json={})
        sid = (await r.json())["id"]
        ws_url = f"ws://{base}/ws/chat?token={token}"
        async with client.session.ws_connect(ws_url, origin="http://127.0.0.1") as ws:
            await ws.send_str(json.dumps({"type": "bogus", "session_id": sid}))
            msg = await asyncio.wait_for(ws.receive(), timeout=5.0)
            payload = json.loads(msg.data)
            assert payload["type"] == "chat.error"
            assert payload["code"] == "unknown_type"


@pytest.mark.asyncio
async def test_ws_chat_session_log_persists_assistant_turn(tmp_path: Path):
    mock_port = _free_port()
    async with _running_mock(mock_port), _running_c1(tmp_path, mock_port) as (client, token, base):
        r = await client.post("/api/sessions", json={})
        sid = (await r.json())["id"]
        await client.patch(f"/api/sessions/{sid}", json={"model": "echo"})
        ws_url = f"ws://{base}/ws/chat?token={token}"
        async with client.session.ws_connect(ws_url, origin="http://127.0.0.1") as ws:
            await ws.send_str(json.dumps({
                "type": "chat.send", "session_id": sid, "text": "ping"
            }))
            for _ in range(50):
                msg = await asyncio.wait_for(ws.receive(), timeout=5.0)
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                payload = json.loads(msg.data)
                if payload.get("type") == "chat.done":
                    break
        # After WS closes, fetch the session and check it has both
        # the user message and the assistant turn.
        r = await client.get(f"/api/sessions/{sid}")
        s = await r.json()
        assert len(s["messages"]) == 2
        assert s["messages"][0]["role"] == "user"
        assert s["messages"][0]["content"] == "ping"
        assert s["messages"][1]["role"] == "assistant"
        assert s["messages"][1]["content"] == "ping"
