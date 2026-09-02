"""Live smoke for the v1.2.0 chat/sessions/secrets surface.

This test starts a real C1 GuiWebCore (with sessions + secrets dirs
and an LLM adapter pointing at the mock) on an ephemeral loopback
port, then exercises the new HTTP routes end-to-end. No browser;
the smoke is a pure HTTP/WS check.

Run with:
    python tests/chat/smoke_v12.py
"""
from __future__ import annotations

import asyncio
import json
import socket
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

# Allow `python tests/chat/smoke_v12.py` to find `tests.fixtures`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import aiohttp

REPO = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@asynccontextmanager
async def _running_mock(port: int):
    from aiohttp import web

    from tests.fixtures.mock_llm import build_app

    runner = web.AppRunner(build_app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


@asynccontextmanager
async def _running_c1(sessions_dir: Path, secrets_dir: Path, llm_url: str):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from dhc.cordis.context import Context
    from dhc.cordis.secrets import SecretsService
    from dhc.modules.c1_gui_web_core.service import GuiWebCore
    from dhc.modules.c7_llm_stream_adapter.service import LLMStreamAdapter
    from dhc.services.session_manager import SessionManager

    sm = SessionManager(sessions_dir)
    ss = SecretsService(secrets_dir)
    gwc = GuiWebCore(
        host="127.0.0.1",
        port=0,
        require_token=True,
        sessions_dir=sessions_dir,
        secrets_dir=secrets_dir,
    )
    gwc.session_manager = sm
    gwc.secrets_service = ss
    gwc.app["ctx"] = Context()
    gwc.app["ctx"].provide("llm", LLMStreamAdapter(base_url=llm_url, api_key="sk-mock-1234567890"))
    server = TestServer(gwc.app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield client, gwc.token, f"{server.host}:{server.port}"
    finally:
        await client.close()


async def main() -> int:
    tmp = Path.cwd() / ".dhc-smoke"
    sessions_dir = tmp / "sessions"
    secrets_dir = tmp / "secrets"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    secrets_dir.mkdir(parents=True, exist_ok=True)
    mock_port = _free_port()
    checks = {"passed": 0, "failed": 0, "details": []}

    def check(name: str, ok: bool, detail: str = "") -> None:
        if ok:
            checks["passed"] += 1
            checks["details"].append(f"PASS: {name}")
        else:
            checks["failed"] += 1
            checks["details"].append(f"FAIL: {name} {detail}")

    async with _running_mock(mock_port) as llm_url:
        async with _running_c1(sessions_dir, secrets_dir, llm_url) as (client, token, base):
            # /healthz
            r = await client.get("/healthz")
            check("/healthz returns 200", r.status == 200)

            # /api/llm/health
            r = await client.get("/api/llm/health")
            body = await r.json()
            check(
                "/api/llm/health reports the mock base_url",
                body.get("ok") is True and body.get("base_url") == llm_url,
                f"body={body}",
            )

            # Create a session.
            r = await client.post("/api/sessions", json={"title": "smoke"})
            sid = (await r.json())["id"]
            check("POST /api/sessions creates a session", r.status == 201 and sid.startswith("s_"))

            # Pin and patch.
            r = await client.patch(f"/api/sessions/{sid}", json={"pinned": True, "model": "echo"})
            body = await r.json()
            check("PATCH /api/sessions pins + sets model", body.get("pinned") is True and body.get("model") == "echo")

            # List pinned first.
            r = await client.get("/api/sessions")
            summaries = (await r.json())["sessions"]
            check("GET /api/sessions lists summaries", any(s["id"] == sid for s in summaries))

            # Send a user message via /api/sessions/{id}/messages and
            # verify the assistant reply echoes it (because the session
            # is pinned to the "echo" model).
            r = await client.post(
                f"/api/sessions/{sid}/messages",
                json={"content": "hello smoke", "model": "echo"},
            )
            check(
                "POST /api/sessions/{id}/messages returns 201",
                r.status == 201,
                f"status={r.status}",
            )
            body = await r.json()
            check(
                "Assistant turn echoed the user text",
                body.get("assistant_message", {}).get("content") == "hello smoke",
                f"body={body}",
            )

            # Secrets: list (empty), put, list (one name), delete.
            r = await client.get("/api/secrets")
            check("GET /api/secrets returns names list", (await r.json())["names"] == [])
            r = await client.put("/api/secrets/openai", json={"value": "sk-leak-12345"})
            check("PUT /api/secrets stores a value", r.status == 204)
            r = await client.get("/api/secrets")
            body = await r.json()
            check(
                "GET /api/secrets shows the name",
                body["names"] == ["openai"],
                f"body={body}",
            )
            check(
                "GET /api/secrets does NOT leak the value",
                "sk-leak-12345" not in str(body),
            )
            r = await client.delete("/api/secrets/openai")
            check("DELETE /api/secrets removes the value", r.status == 204)
            r = await client.get("/api/secrets")
            check("GET /api/secrets empty after delete", (await r.json())["names"] == [])

            # /ws/chat: connect, send, receive, done.
            ws_url = f"ws://{base}/ws/chat?token={token}"
            async with client.session.ws_connect(ws_url, origin="http://127.0.0.1") as ws:
                r2 = await client.post("/api/sessions", json={"title": "ws-smoke"})
                sid2 = (await r2.json())["id"]
                await client.patch(f"/api/sessions/{sid2}", json={"model": "echo"})
                await ws.send_str(json.dumps({
                    "type": "chat.send",
                    "session_id": sid2,
                    "text": "ping",
                }))
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
                        check("WS chat does not error", False, f"err={payload}")
                        break
                check("WS /ws/chat streams the assistant reply", "".join(deltas) == "ping")
                check("WS /ws/chat sends chat.done", done_seen)

                # Session log has both user + assistant turns.
                r3 = await client.get(f"/api/sessions/{sid2}")
                s3 = await r3.json()
                check(
                    "Session log has 2 messages after WS chat",
                    len(s3["messages"]) == 2,
                    f"got {len(s3['messages'])}",
                )
                check(
                    "Assistant turn in session log is persisted",
                    s3["messages"][1]["content"] == "ping",
                )

    # On disk: the secrets.log was written, the session file was written.
    log = secrets_dir / "secrets.log"
    check("secrets.log exists", log.exists())
    sess_files = list(sessions_dir.glob("sessions/*.json"))
    check("At least one session JSON file was written", len(sess_files) >= 1)

    print("\n".join(checks["details"]))
    print(f"\nPass: {checks['passed']}  Fail: {checks['failed']}")
    return 0 if checks["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
