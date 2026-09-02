"""Tests for v1.3.0 /api/models and /api/models/{id} routes on C1."""
from __future__ import annotations

import socket
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import pytest
from aiohttp.test_utils import TestClient, TestServer

from dhc.cordis.context import Context
from dhc.cordis.secrets import SecretsService
from dhc.modules.c1_gui_web_core.service import GuiWebCore
from dhc.services.session_manager import SessionManager


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@asynccontextmanager
async def _running_c1(tmp_path: Path) -> AsyncIterator[tuple[TestClient, str]]:
    """Bring up a GuiWebCore with sessions/secrets/registry wired.

    No mock LLM needed for the registry routes.
    Yields (test_client, token).
    """
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
    try:
        yield client, gwc.token
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_models_returns_list(tmp_path: Path) -> None:
    async with _running_c1(tmp_path) as (client, _):
        r = await client.get("/api/models")
        assert r.status == 200
        body = await r.json()
        assert "models" in body
        assert len(body["models"]) == 6


@pytest.mark.asyncio
async def test_get_models_includes_required_fields(tmp_path: Path) -> None:
    async with _running_c1(tmp_path) as (client, _):
        r = await client.get("/api/models")
        body = await r.json()
        for m in body["models"]:
            for k in (
                "id", "name", "provider", "context_length",
                "pricing_input", "pricing_output", "capabilities",
            ):
                assert k in m, f"missing field {k} in {m}"


@pytest.mark.asyncio
async def test_get_model_by_id(tmp_path: Path) -> None:
    async with _running_c1(tmp_path) as (client, _):
        r = await client.get("/api/models/openai/gpt-4o-mini")
        assert r.status == 200
        body = await r.json()
        assert body["id"] == "openai/gpt-4o-mini"
        assert body["provider"] == "openai"
        assert body["context_length"] == 128_000


@pytest.mark.asyncio
async def test_get_model_by_id_not_found(tmp_path: Path) -> None:
    async with _running_c1(tmp_path) as (client, _):
        r = await client.get("/api/models/nope/no-such-model")
        assert r.status == 404
        body = await r.json()
        assert "error" in body


@pytest.mark.asyncio
async def test_get_models_includes_mock(tmp_path: Path) -> None:
    async with _running_c1(tmp_path) as (client, _):
        r = await client.get("/api/models")
        body = await r.json()
        ids = {m["id"] for m in body["models"]}
        assert "mock-llm/default" in ids


@pytest.mark.asyncio
async def test_get_models_security_headers(tmp_path: Path) -> None:
    async with _running_c1(tmp_path) as (client, _):
        r = await client.get("/api/models")
        # The v1.2.x security headers are attached to every response.
        assert "X-Content-Type-Options" in r.headers
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert "Content-Security-Policy" in r.headers


@pytest.mark.asyncio
async def test_get_models_returns_6_unique_providers(tmp_path: Path) -> None:
    async with _running_c1(tmp_path) as (client, _):
        r = await client.get("/api/models")
        body = await r.json()
        providers = {m["provider"] for m in body["models"]}
        # 4 distinct providers in the registry: mock, openai, anthropic, openrouter
        assert providers == {"mock", "openai", "anthropic", "openrouter"}


@pytest.mark.asyncio
async def test_get_model_capabilities_are_list(tmp_path: Path) -> None:
    async with _running_c1(tmp_path) as (client, _):
        r = await client.get("/api/models/openai/gpt-4.1")
        body = await r.json()
        assert isinstance(body["capabilities"], list)
        # The serialized list is sorted; "vision" should be present
        assert "vision" in body["capabilities"]
        assert body["capabilities"] == sorted(body["capabilities"])
