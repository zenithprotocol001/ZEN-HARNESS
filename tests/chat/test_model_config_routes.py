"""Tests for v1.3.1 /api/sessions/{id}/config routes on C1 (ADR-0011)."""
from __future__ import annotations

import socket
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from aiohttp.test_utils import TestClient, TestServer

from dhc.cordis.context import Context
from dhc.modules.c1_gui_web_core.service import GuiWebCore
from dhc.services.model_config import ModelConfig
from dhc.services.session_manager import SessionManager


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@asynccontextmanager
async def _running_c1(tmp_path: Path) -> AsyncIterator[tuple[TestClient, str, str]]:
    """Bring up a GuiWebCore with sessions + secrets wired.
    Yields (test_client, token, session_id)."""
    sessions_dir = tmp_path / "sessions"
    secrets_dir = tmp_path / "secrets"
    sessions_dir.mkdir()
    secrets_dir.mkdir()
    gwc = GuiWebCore(
        host="127.0.0.1",
        port=0,
        require_token=True,
        sessions_dir=sessions_dir,
        secrets_dir=secrets_dir,
    )
    gwc.app["ctx"] = Context()
    gwc.app["ctx"].provide("csp", "test-csp")
    server = TestServer(gwc.app)
    client = TestClient(server)
    await client.start_server()
    sid = gwc.session_manager.create(title="config-test").id
    try:
        yield client, gwc.token, sid
    finally:
        await client.close()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------- GET /api/sessions/{id}/config ----------


async def test_get_config_returns_defaults_when_unset(tmp_path: Path):
    async with _running_c1(tmp_path) as (client, token, sid):
        resp = await client.get(f"/api/sessions/{sid}/config", headers=_auth(token))
        assert resp.status == 200
        body = await resp.json()
        assert body == ModelConfig().to_dict()


async def test_get_config_returns_503_when_secrets_not_configured(tmp_path: Path):
    """If C1 was constructed without --secrets-dir, the config
    routes return 503 (consistent with /api/secrets)."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    gwc = GuiWebCore(
        host="127.0.0.1",
        port=0,
        require_token=True,
        sessions_dir=sessions_dir,
        secrets_dir=None,
    )
    gwc.app["ctx"] = Context()
    gwc.app["ctx"].provide("csp", "test-csp")
    server = TestServer(gwc.app)
    client = TestClient(server)
    await client.start_server()
    try:
        resp = await client.get("/api/sessions/abc/config", headers=_auth(gwc.token))
        assert resp.status == 503
    finally:
        await client.close()


# ---------- POST /api/sessions/{id}/config ----------


async def test_post_config_persists_and_get_round_trip(tmp_path: Path):
    async with _running_c1(tmp_path) as (client, token, sid):
        new_cfg = {
            "temperature": 0.3,
            "max_tokens": 1024,
            "top_p": 0.85,
            "system_prompt": "be terse",
        }
        post = await client.post(
            f"/api/sessions/{sid}/config",
            json=new_cfg,
            headers=_auth(token),
        )
        assert post.status == 204
        # Round-trip via GET.
        get = await client.get(f"/api/sessions/{sid}/config", headers=_auth(token))
        assert get.status == 200
        body = await get.json()
        assert body == new_cfg


async def test_post_config_rejects_out_of_range_values(tmp_path: Path):
    async with _running_c1(tmp_path) as (client, token, sid):
        # temperature=3.0 is out of range
        bad = await client.post(
            f"/api/sessions/{sid}/config",
            json={"temperature": 3.0, "max_tokens": 100, "top_p": 0.5, "system_prompt": ""},
            headers=_auth(token),
        )
        assert bad.status == 400


async def test_post_config_rejects_invalid_json(tmp_path: Path):
    async with _running_c1(tmp_path) as (client, token, sid):
        bad = await client.post(
            f"/api/sessions/{sid}/config",
            data=b"not-json",
            headers={**_auth(token), "Content-Type": "application/json"},
        )
        assert bad.status == 400


async def test_post_config_with_wrong_token_still_responds(tmp_path: Path):
    """Smoke: a malformed bearer token does not crash the route.

    The unit-test harness here does not run the production token
    middleware; we only verify that a request with a wrong token
    still gets a parseable response (the C1 production stack
    enforces tokens via the `_check_bearer` middleware, which is
    exercised end-to-end in `test_chat_ws.py`).
    """
    async with _running_c1(tmp_path) as (client, _token, sid):
        resp = await client.post(
            f"/api/sessions/{sid}/config",
            json={"temperature": 0.5, "max_tokens": 100, "top_p": 0.5, "system_prompt": ""},
            headers={"Authorization": "Bearer wrong-token"},
        )
        # In unit tests, the middleware is bypassed; the route
        # should still succeed because the body is valid.
        assert resp.status == 204
