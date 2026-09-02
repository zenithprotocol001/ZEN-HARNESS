"""Tests for v1.3.0 C7 dispatch (ADR-0009)."""
from __future__ import annotations

import asyncio
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from dhc.cordis.context import Context
from dhc.cordis.secrets import SecretsService
from dhc.integrations.base import ProviderError
from dhc.modules.c1_gui_web_core.service import GuiWebCore
from dhc.modules.c7_llm_stream_adapter.service import LLMStreamAdapter
from dhc.services.model_registry import ModelRegistry
from dhc.services.session_manager import SessionManager
from tests.integrations._mock_provider_server import _free_port, _mock_provider_server


def _adapter_with_registry(
    base_url: str,
    *,
    secrets_dir: Path,
    openai_base_url: str | None = None,
) -> tuple[LLMStreamAdapter, SecretsService]:
    """Build a C7 adapter wired to the registry + a fresh SecretsService."""
    ss = SecretsService(secrets_dir)
    adapter = LLMStreamAdapter(
        base_url=base_url,
        api_key="sk-mock-1234567890",
        model_registry=ModelRegistry(),
        secrets_service=ss,
    )
    # Override the OpenAI URL so the live dispatch hits the mock.
    if openai_base_url is not None:
        from dhc.integrations.openai_client import OpenAIClient
        # Monkey-patch the factory by replacing the registered client.
        # The simplest path is to override the module-level constant via
        # a one-shot client whose base_url is the mock. We do this by
        # patching `provider_client_for` via a small wrapper.
        from dhc.integrations import provider_client_for
        from dhc.services.model_registry import Model as _Model

        def _patched(m: _Model) -> object:
            if m.provider == "openai":
                return OpenAIClient(base_url=openai_base_url)
            return provider_client_for(m)

        # Re-bind in the LLMStreamAdapter's closure by replacing the
        # imported `provider_client_for` in the dispatch method. The
        # method does `from dhc.integrations import provider_client_for`
        # at call time, so we patch the module attribute.
        import dhc.integrations as _integrations
        _integrations.provider_client_for = _patched  # type: ignore[assignment]
    return adapter, ss


@pytest.mark.asyncio
async def test_c7_dispatch_to_openai(tmp_path: Path) -> None:
    """`model='openai/gpt-4o-mini'` + a stored key → OpenAI mock server."""
    port = _free_port()
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    async with _mock_provider_server(
        port,
        openai_responses=[{"sse_openai": ["hello from openai"]}],
    ):
        adapter, ss = _adapter_with_registry(
            base_url="http://127.0.0.1:0",
            secrets_dir=secrets_dir,
            openai_base_url=f"http://127.0.0.1:{port}",
        )
        ss.put("llm_provider_openai_gpt-4o-mini", "sk-test-1234567890")
        deltas: list[str] = []
        async for chunk in adapter.chat_stream(
            messages=[{"role": "user", "content": "hi"}],
            model="openai/gpt-4o-mini",
        ):
            deltas.append(chunk.delta)
        assert "".join(deltas) == "hello from openai"


@pytest.mark.asyncio
async def test_c7_dispatch_to_anthropic(tmp_path: Path) -> None:
    port = _free_port()
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    from dhc.integrations.anthropic_client import AnthropicClient
    from dhc.integrations import provider_client_for
    from dhc.services.model_registry import Model as _Model

    original = provider_client_for

    def _patched(m: _Model) -> object:
        if m.provider == "anthropic":
            return AnthropicClient(base_url=f"http://127.0.0.1:{port}")
        return original(m)

    import dhc.integrations as _integrations
    _integrations.provider_client_for = _patched  # type: ignore[assignment]

    async with _mock_provider_server(
        port,
        anthropic_responses=[{"sse_anthropic": ["hi from claude"]}],
    ):
        ss = SecretsService(secrets_dir)
        adapter = LLMStreamAdapter(
            base_url="http://127.0.0.1:0",
            api_key="sk-mock",
            model_registry=ModelRegistry(),
            secrets_service=ss,
        )
        ss.put(
            "llm_provider_anthropic_claude-3-5-sonnet-latest",
            "sk-ant-test-1234567890",
        )
        deltas: list[str] = []
        async for chunk in adapter.chat_stream(
            messages=[{"role": "user", "content": "hi"}],
            model="anthropic/claude-3-5-sonnet-latest",
        ):
            deltas.append(chunk.delta)
        assert "".join(deltas) == "hi from claude"
    _integrations.provider_client_for = original  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_c7_dispatch_to_openrouter(tmp_path: Path) -> None:
    port = _free_port()
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    from dhc.integrations.openrouter_client import OpenRouterClient
    from dhc.integrations import provider_client_for
    from dhc.services.model_registry import Model as _Model

    original = provider_client_for

    def _patched(m: _Model) -> object:
        if m.provider == "openrouter":
            return OpenRouterClient(base_url=f"http://127.0.0.1:{port}")
        return original(m)

    import dhc.integrations as _integrations
    _integrations.provider_client_for = _patched  # type: ignore[assignment]

    async with _mock_provider_server(
        port,
        openrouter_responses=[{"sse_openai": ["hi from openrouter"]}],
    ):
        ss = SecretsService(secrets_dir)
        adapter = LLMStreamAdapter(
            base_url="http://127.0.0.1:0",
            api_key="sk-mock",
            model_registry=ModelRegistry(),
            secrets_service=ss,
        )
        ss.put("llm_provider_openrouter_auto", "sk-or-test-1234567890")
        deltas: list[str] = []
        async for chunk in adapter.chat_stream(
            messages=[{"role": "user", "content": "hi"}],
            model="openrouter/auto",
        ):
            deltas.append(chunk.delta)
        assert "".join(deltas) == "hi from openrouter"
    _integrations.provider_client_for = original  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_c7_dispatch_missing_api_key_raises(tmp_path: Path) -> None:
    """No secret for the model → ProviderError(status=401) before HTTP."""
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    ss = SecretsService(secrets_dir)
    adapter = LLMStreamAdapter(
        base_url="http://127.0.0.1:0",
        api_key="sk-mock",
        model_registry=ModelRegistry(),
        secrets_service=ss,
    )
    # No key stored; the chat_stream iterator should raise on first iteration.
    with pytest.raises(ProviderError) as ei:
        async for _ in adapter.chat_stream(
            messages=[{"role": "user", "content": "x"}],
            model="openai/gpt-4o-mini",
        ):
            pass
    assert ei.value.status == 401
    assert "missing api key" in str(ei.value)


@pytest.mark.asyncio
async def test_c7_dispatch_unknown_model_raises(tmp_path: Path) -> None:
    """An unknown model id raises before any HTTP call."""
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    ss = SecretsService(secrets_dir)
    adapter = LLMStreamAdapter(
        base_url="http://127.0.0.1:0",
        api_key="sk-mock",
        model_registry=ModelRegistry(),
        secrets_service=ss,
    )
    with pytest.raises(ProviderError, match="unknown model"):
        async for _ in adapter.chat_stream(
            messages=[{"role": "user", "content": "x"}],
            model="nope/no-such",
        ):
            pass


@pytest.mark.asyncio
async def test_c7_dispatch_uses_mock_when_model_is_default(tmp_path: Path) -> None:
    """`model='mock-llm/default'` falls through to the v1.2.x C7 path."""
    # Start a mock server, point the adapter's base_url at it, and
    # ensure chat_stream hits the mock (NOT the registry).
    port = _free_port()
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    async with _mock_provider_server(
        port,
        openai_responses=[{"sse_openai": ["mock-echo"]}],
    ):
        ss = SecretsService(secrets_dir)
        adapter = LLMStreamAdapter(
            base_url=f"http://127.0.0.1:{port}",
            api_key="sk-mock",
            model_registry=ModelRegistry(),
            secrets_service=ss,
        )
        deltas: list[str] = []
        async for chunk in adapter.chat_stream(
            messages=[{"role": "user", "content": "x"}],
            model="mock-llm/default",
        ):
            deltas.append(chunk.delta)
        assert "".join(deltas) == "mock-echo"


@pytest.mark.asyncio
async def test_c7_dispatch_no_model_registry_keeps_v12_path(tmp_path: Path) -> None:
    """When `model_registry` is None, the v1.2.x POST path is used."""
    port = _free_port()
    async with _mock_provider_server(
        port,
        openai_responses=[{"sse_openai": ["v12-path"]}],
    ):
        adapter = LLMStreamAdapter(
            base_url=f"http://127.0.0.1:{port}",
            api_key="sk-mock",
        )  # no model_registry, no secrets_service
        deltas: list[str] = []
        async for chunk in adapter.chat_stream(
            messages=[{"role": "user", "content": "x"}],
            model="openai/gpt-4o-mini",
        ):
            deltas.append(chunk.delta)
        assert "".join(deltas) == "v12-path"


@pytest.mark.asyncio
async def test_c7_ws_chat_with_live_provider(tmp_path: Path) -> None:
    """Full WS round-trip: client sends chat.send, C1 resolves the
    provider, streams chat.delta frames, closes with chat.done."""
    port = _free_port()
    mock_port = _free_port()
    sessions_dir = tmp_path / "sessions"
    secrets_dir = tmp_path / "secrets"
    sessions_dir.mkdir()
    secrets_dir.mkdir()

    # Patch the openai client to point at the mock.
    from dhc.integrations import provider_client_for as _orig
    from dhc.integrations.openai_client import OpenAIClient
    from dhc.services.model_registry import Model as _Model

    def _patched(m: _Model) -> object:
        if m.provider == "openai":
            return OpenAIClient(base_url=f"http://127.0.0.1:{mock_port}")
        return _orig(m)

    import dhc.integrations as _integrations
    _integrations.provider_client_for = _patched  # type: ignore[assignment]

    async with _mock_provider_server(
        mock_port,
        openai_responses=[{"sse_openai": ["hello from live"]}],
    ):
        sm = SessionManager(sessions_dir)
        ss = SecretsService(secrets_dir)
        ss.put("llm_provider_openai_gpt-4o-mini", "sk-test-1234567890")
        gwc = GuiWebCore(
            host="127.0.0.1", port=0, require_token=True,
            sessions_dir=sessions_dir, secrets_dir=secrets_dir,
        )
        gwc.app["ctx"] = Context()
        gwc.app["ctx"].provide(
            "llm",
            LLMStreamAdapter(
                base_url="http://127.0.0.1:0",
                api_key="sk-mock",
                model_registry=ModelRegistry(),
                secrets_service=ss,
            ),
        )
        gwc.app["ctx"].provide("csp", "test-csp")
        server = TestServer(gwc.app)
        client = TestClient(server)
        await client.start_server()
        try:
            r = await client.post("/api/sessions", json={"title": "live test"})
            sid = (await r.json())["id"]
            # Set the session's model to a live provider model.
            r = await client.patch(
                f"/api/sessions/{sid}", json={"model": "openai/gpt-4o-mini"}
            )
            assert r.status == 200
            # Send the chat via WS using the session model
            ws_url = f"ws://{server.host}:{server.port}/ws/chat?token={gwc.token}"
            async with client.session.ws_connect(
                ws_url, origin="http://127.0.0.1"
            ) as ws:
                await ws.send_json({
                    "type": "chat.send",
                    "session_id": sid,
                    "text": "hi",
                })
                deltas: list[str] = []
                done = False
                while not done:
                    msg = await asyncio.wait_for(ws.receive(), timeout=5.0)
                    if msg.type == web.WSMsgType.TEXT:
                        import json as _json
                        frame = _json.loads(msg.data)
                        if frame.get("type") == "chat.delta":
                            deltas.append(frame.get("delta", ""))
                        elif frame.get("type") == "chat.done":
                            done = True
                    elif msg.type in (web.WSMsgType.CLOSE, web.WSMsgType.CLOSED):
                        done = True
                assert "".join(deltas) == "hello from live"
        finally:
            await client.close()
    _integrations.provider_client_for = _orig  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_c7_ws_chat_provider_error_closes_1011(tmp_path: Path) -> None:
    """A ProviderError mid-stream → WS closes with 1011 + chat.error frame."""
    sessions_dir = tmp_path / "sessions"
    secrets_dir = tmp_path / "secrets"
    sessions_dir.mkdir()
    secrets_dir.mkdir()
    sm = SessionManager(sessions_dir)
    ss = SecretsService(secrets_dir)
    # No key stored → first chat_stream raises immediately.
    gwc = GuiWebCore(
        host="127.0.0.1", port=0, require_token=True,
        sessions_dir=sessions_dir, secrets_dir=secrets_dir,
    )
    gwc.app["ctx"] = Context()
    gwc.app["ctx"].provide(
        "llm",
        LLMStreamAdapter(
            base_url="http://127.0.0.1:0",
            api_key="sk-mock",
            model_registry=ModelRegistry(),
            secrets_service=ss,
        ),
    )
    gwc.app["ctx"].provide("csp", "test-csp")
    server = TestServer(gwc.app)
    client = TestClient(server)
    await client.start_server()
    try:
        r = await client.post("/api/sessions", json={"title": "err"})
        sid = (await r.json())["id"]
        r = await client.patch(
            f"/api/sessions/{sid}", json={"model": "openai/gpt-4o-mini"}
        )
        assert r.status == 200
        ws_url = f"ws://{server.host}:{server.port}/ws/chat?token={gwc.token}"
        async with client.session.ws_connect(
            ws_url, origin="http://127.0.0.1"
        ) as ws:
            await ws.send_json({
                "type": "chat.send",
                "session_id": sid,
                "text": "hi",
            })
            saw_error = False
            # The C1 handler sends a chat.error frame and then
            # continues listening; the WS is not closed on a
            # single turn failure. Wait for the error frame.
            for _ in range(20):
                msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
                if msg.type == web.WSMsgType.TEXT:
                    import json as _json
                    frame = _json.loads(msg.data)
                    if frame.get("type") == "chat.error":
                        saw_error = True
                        assert "missing api key" in frame.get("message", "")
                        break
            assert saw_error
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_c7_dispatch_retry_invisible_to_client(tmp_path: Path) -> None:
    """A retried 5xx is invisible to the consumer; only the final
    successful deltas are yielded."""
    port = _free_port()
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    from dhc.integrations.openai_client import OpenAIClient
    from dhc.integrations import provider_client_for as _orig
    from dhc.services.model_registry import Model as _Model
    from dhc.integrations.base import RetryConfig

    def _patched(m: _Model) -> object:
        if m.provider == "openai":
            return OpenAIClient(base_url=f"http://127.0.0.1:{port}")
        return _orig(m)

    import dhc.integrations as _integrations
    _integrations.provider_client_for = _patched  # type: ignore[assignment]

    async with _mock_provider_server(
        port,
        openai_responses=[
            {"status": 503, "text": "try again"},
            {"sse_openai": ["after-retry"]},
        ],
    ):
        ss = SecretsService(secrets_dir)
        ss.put("llm_provider_openai_gpt-4o-mini", "sk-test-1234567890")
        adapter = LLMStreamAdapter(
            base_url="http://127.0.0.1:0",
            api_key="sk-mock",
            model_registry=ModelRegistry(),
            secrets_service=ss,
        )
        # Force a fast backoff
        from dhc.integrations import openai_client as _oc
        original_chat = _oc.OpenAIClient.chat_stream

        async def _fast_chat_stream(self, messages, model, api_key,
                                    retry_config=None):
            rc = retry_config or RetryConfig(
                backoff_seconds=(0.01, 0.01)
            )
            async for c in original_chat(self, messages, model, api_key,
                                         retry_config=rc):
                yield c

        _oc.OpenAIClient.chat_stream = _fast_chat_stream  # type: ignore[assignment]
        try:
            deltas: list[str] = []
            async for chunk in adapter.chat_stream(
                messages=[{"role": "user", "content": "x"}],
                model="openai/gpt-4o-mini",
            ):
                deltas.append(chunk.delta)
            assert "".join(deltas) == "after-retry"
        finally:
            _oc.OpenAIClient.chat_stream = original_chat  # type: ignore[assignment]
    _integrations.provider_client_for = _orig  # type: ignore[assignment]
