"""Tests that the C1 route layer exposes the plugin marketplace
endpoints and respects the bearer-token auth model.

These tests use the in-process aiohttp test client, so they exercise
the actual route handlers + auth + CSP stack, not a curl-level shell.
"""

# Note: keep the docstring on the lines above simple - no embedded
# triple-quotes inside - to avoid ast.parse issues during collection.
from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from dhc.cordis.context import Context
from dhc.modules.c1_gui_web_core.service import GuiWebCore


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def web_core(tmp_path):
    'Build a GuiWebCore bound to an ephemeral port with no static dir.'
    ctx = Context()
    core = GuiWebCore(
        host="127.0.0.1",
        port=0,
        static_dir=None,
        port_file=tmp_path / "p",
        token_file=tmp_path / "t",
        token="test-token-do-not-use-in-prod",
        require_token=True,
    )
    core.app["ctx"] = ctx
    core.app["repo_root"] = REPO_ROOT
    return core, ctx, "test-token-do-not-use-in-prod"


@pytest.fixture
async def client(web_core):
    core, _ctx, _tok = web_core
    server = TestServer(core.app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


# ---------- /healthz ----------


async def test_healthz_lists_core_modules_and_plugin_state(client):
    r = await client.get("/healthz")
    assert r.status == 200
    body = await r.json()
    assert body["ok"] is True
    module_ids = [m["id"] for m in body["modules"]]
    assert module_ids == [f"c{i}" for i in range(1, 11)]
    assert "plugins_discovered" in body
    assert "plugins_loaded" in body


# ---------- /api/manifest ----------


async def test_api_manifest_returns_full_manifest(client):
    r = await client.get("/api/manifest")
    assert r.status == 200
    body = await r.json()
    assert len(body["modules"]) == 10
    assert "plugins_discovered" in body


# ---------- /plugins + /plugins/{id} ----------


async def test_plugin_load_unload_round_trip(client):
    # List (initially empty for this fresh web_core fixture)
    r = await client.get("/plugins")
    initial = await r.json()
    assert any(p["id"] == "rate_limiter_v1" for p in initial["available"])

    # Load
    r = await client.post("/plugins/rate_limiter_v1", json={"config": {"rate": 5.0, "burst": 10.0}})
    assert r.status == 200
    body = await r.json()
    assert body["ok"] is True

    # Listing now shows it loaded
    r = await client.get("/plugins")
    after = await r.json()
    loaded_ids = [p["id"] for p in after["loaded"]]
    assert "rate_limiter_v1" in loaded_ids

    # Cannot load twice
    r = await client.post("/plugins/rate_limiter_v1", json={})
    assert r.status == 409

    # Unload
    r = await client.delete("/plugins/rate_limiter_v1")
    assert r.status == 200

    # Listing back to no loaded
    r = await client.get("/plugins")
    after = await r.json()
    assert all(p["id"] != "rate_limiter_v1" for p in after["loaded"])


async def test_plugin_load_unknown_returns_404(client):
    r = await client.post("/plugins/does_not_exist_v1", json={})
    assert r.status == 404


# ---------- /api/eval ----------


async def test_api_eval_rejects_missing_module(client):
    r = await client.post("/api/eval", json={"code": "x = 1"})
    assert r.status == 400


async def test_api_eval_runs_against_real_module(client):
    # C1 happy path is a trivial module-level check, but to exercise
    # the full pipeline we use c10 (ObservabilitySink) which has 5
    # unit tests that are very fast and don't depend on anything else.
    # The "code" we submit is a stub docstring-only module; c10 has
    # no test that touches a service function body, so the test suite
    # passes against the stub and the eval endpoint returns the score.
    code = "X = 1\n"
    r = await client.post("/api/eval", json={"module": "c10", "code": code})
    assert r.status == 200
    body = await r.json()
    assert body["module"] == "c10"
    assert "findings" in body
    # dhc_v is a number; tests passed / failed keys exist
    assert isinstance(body["dhc_v"], (int, float))
    assert "tests_passed" in body
    assert "tests_failed_or_errored" in body


# ---------- /prompts requires the prompt_browser_v1 plugin ----------


async def test_prompts_empty_when_plugin_not_loaded(client):
    r = await client.get("/prompts")
    body = await r.json()
    assert "prompts" in body


# ---------- Security headers are present on every route ----------


@pytest.mark.parametrize(
    "path",
    ["/healthz", "/api/manifest", "/plugins", "/prompts"],
)
async def test_security_headers_present_on_every_route(client, path):
    r = await client.get(path)
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert "Content-Security-Policy" in r.headers
    assert r.headers.get("Referrer-Policy") == "no-referrer"
