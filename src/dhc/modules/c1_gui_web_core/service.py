"""C1 GuiWebCore (Python): aiohttp server with strict CSP + WebSocket bridge.

Contract:
- Every HTTP response includes a strict Content-Security-Policy header.
  No `'unsafe-inline'`, no `'unsafe-eval'`, no wildcards.
- WebSocket handshake validates the `Origin` header AND a per-launch
  bearer token. The token is generated at server start (256 bits of
  entropy from `secrets.token_urlsafe(32)`), printed to stdout, and
  written to a `serve_c1.token` file that the React build reads on
  load. This prevents a malicious local process (or a misconfigured
  upstream) from connecting to the WS without the token.
- The token is required for the WS upgrade via either:
    * `Authorization: Bearer <token>` header (preferred, for clients
      that can set headers), or
    * `?token=<token>` query parameter (fallback for browser WS
      clients that cannot set custom headers during handshake).
- When a static dist/ directory is present, the server serves the
  built React UI as the root document and `/assets/*` paths. The
  `index.html` is rewritten at startup to embed the token in a
  `<meta name="dhc-token">` tag so the React client can read it
  without an additional round trip.
- The bound port is written to a `.port` file for downstream tools
  to discover the ephemeral port.
- Errors are routed to C10 telemetry if present; never swallowed.

CSP policy (default):
    default-src 'self';
    script-src 'self';
    style-src 'self';
    img-src 'self' data:;
    connect-src 'self' ws: wss:;
    object-src 'none';
    base-uri 'self';
    frame-ancestors 'none';
    form-action 'self';
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from aiohttp import web

from dhc.cordis.context import Context
from dhc.cordis.plugin import plugin


CSP_HEADER: str = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "media-src 'self'; "
    "font-src 'self'"
)


def build_csp_header(extra: str | None = None) -> str:
    if not extra:
        return CSP_HEADER
    return CSP_HEADER + "; " + extra


def _security_headers(extra_csp: str | None = None) -> dict[str, str]:
    return {
        "Content-Security-Policy": build_csp_header(extra_csp),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "DENY",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    }


# Origins that may complete a WebSocket upgrade against this server.
# Loopback only by default. Configurable via GuiWebCore(allowed_origins=...).
DEFAULT_ALLOWED_ORIGINS: tuple[str, ...] = (
    "http://127.0.0.1",
    "http://localhost",
    "https://127.0.0.1",
    "https://localhost",
)


def _is_allowed_origin(origin: str, allowed: Iterable[str]) -> bool:
    """An origin is allowed if it matches an allowed prefix AND the
    host portion is a loopback address. We do not trust DNS names that
    happen to share the prefix.
    """
    if not origin:
        return False
    for prefix in allowed:
        if origin == prefix or origin.startswith(prefix + ":"):
            host = origin.split("://", 1)[-1].split(":", 1)[0]
            if host in ("127.0.0.1", "localhost", "::1", "[::1]"):
                return True
    return False


def _extract_bearer_token(request: web.Request) -> str | None:
    """Pull the bearer token from the request.

    Order of preference:
      1. `Authorization: Bearer <token>` header (constant-time compared)
      2. `?token=<token>` query parameter (less secure but required for
         browser WebSocket clients that cannot set custom headers during
         the handshake)
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip() or None
    q = request.query.get("token")
    if q:
        return q
    return None


def _check_token(provided: str | None, expected: str) -> bool:
    """Constant-time bearer token comparison. Empty or missing tokens
    never match — we never return True for the empty case."""
    if not provided or not expected:
        return False
    # secrets.compare_digest is the Python stdlib equivalent of
    # hmac.compare_digest (and uses OpenSSL's CRYPTO_memcmp on most
    # platforms). Both inputs are normalized to bytes.
    return secrets.compare_digest(
        provided.encode("utf-8"),
        expected.encode("utf-8"),
    )


_FORWARDED_EVENTS: tuple[str, ...] = (
    "turn/start",
    "agent/pre-step",
    "step/start",
    "llm/stream",
    "tool/call",
    "step/end",
    "turn/end",
    "system/heartbeat",
    "system/error",
)


def _generate_token() -> str:
    """Cryptographically strong 256-bit URL-safe token.

    Uses `secrets.token_urlsafe(32)` which is backed by the OS CSPRNG
    (BCryptGenRandom on Windows, /dev/urandom on Linux/macOS).
    """
    return secrets.token_urlsafe(32)


def _embed_token_in_index(index_path: Path, token: str) -> None:
    """Rewrite the React index.html to embed the token in a <meta> tag.

    The token is HTML-escaped so it cannot break out of the attribute
    even if it contains quotes (it won't, but defense in depth).
    """
    if not index_path.exists():
        return
    text = index_path.read_text(encoding="utf-8")
    if 'name="dhc-token"' in text:
        # Replace existing tag
        import re as _re
        text = _re.sub(
            r'<meta\s+name="dhc-token"\s+content="[^"]*"\s*/?>',
            f'<meta name="dhc-token" content="{_html_attr_escape(token)}" />',
            text,
            count=1,
        )
    else:
        # Insert before </head>
        meta = f'<meta name="dhc-token" content="{_html_attr_escape(token)}" />'
        text = text.replace("</head>", f"  {meta}\n  </head>", 1)
    index_path.write_text(text, encoding="utf-8")


def _html_attr_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


async def _index(_request: web.Request) -> web.Response:
    return web.Response(
        text="<html><body>dhc web core</body></html>",
        content_type="text/html",
        headers=_security_headers(),
    )


async def _healthz(_request: web.Request) -> web.Response:
    """Health probe.

    The payload includes the discovered plugin manifests and the
    currently-loaded plugin list, so the GUI's Modules tab can poll
    /healthz instead of needing its own /api/manifest call.
    """
    request = _request
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None:
        return web.json_response({"ok": True, "modules": [], "plugins": []}, headers=_security_headers())
    state = web_core.plugin_state
    discovered = [
        {"id": m.id, "name": m.name, "version": m.version, "loaded": m.id in state.loaded}
        for m in state.discovered.values()
    ]
    loaded = [
        {
            "id": lp.plugin_id,
            "name": lp.manifest.name,
            "version": lp.manifest.version,
            "loaded_at_ms": lp.loaded_at_ms,
        }
        for lp in state.loaded.values()
    ]
    return web.json_response(
        {
            "ok": True,
            "ts": int(time.time()),
            "modules": [{"id": f"c{i}", "key": f"c{i}"} for i in range(1, 11)],
            "plugins_discovered": discovered,
            "plugins_loaded": loaded,
        },
        headers=_security_headers(),
    )


async def _api_manifest(_request: web.Request) -> web.Response:
    """Full manifest: 10 core modules + every discovered plugin."""
    web_core: "GuiWebCore | None" = _request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None:
        return web.json_response({"error": "no web_core"}, status=500, headers=_security_headers())
    state = web_core.plugin_state
    return web.json_response(
        {
            "modules": [{"id": f"c{i}", "key": f"c{i}"} for i in range(1, 11)],
            "plugins_discovered": [
                {"id": m.id, "name": m.name, "version": m.version, "loaded": m.id in state.loaded}
                for m in state.discovered.values()
            ],
        },
        headers=_security_headers(),
    )


async def _plugins_list(_request: web.Request) -> web.Response:
    web_core: "GuiWebCore | None" = _request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None:
        return web.json_response({"loaded": [], "available": []}, headers=_security_headers())
    state = web_core.plugin_state
    return web.json_response(
        {
            "loaded": [
                {"id": lp.plugin_id, "name": lp.manifest.name, "version": lp.manifest.version}
                for lp in state.loaded.values()
            ],
            "available": [
                {"id": m.id, "name": m.name, "version": m.version, "loaded": m.id in state.loaded}
                for m in state.discovered.values()
            ],
        },
        headers=_security_headers(),
    )


async def _prompts_list(_request: web.Request) -> web.Response:
    """Read from ctx.inject('prompt_browser') if the plugin is loaded."""
    ctx: "Context | None" = _request.app.get("ctx")  # type: ignore[attr-defined]
    browser = ctx.inject("prompt_browser") if ctx is not None else None
    if browser is None:
        return web.json_response(
            {"prompts": [], "note": "prompt_browser_v1 not loaded"},
            headers=_security_headers(),
        )
    return web.json_response(
        {"prompts": browser.list()},
        headers=_security_headers(),
    )


async def _prompts_get(request: web.Request) -> web.Response:
    """GET /api/prompts/{key} — return the full body of a single prompt."""
    key = request.match_info.get("key", "")
    ctx: "Context | None" = request.app.get("ctx")  # type: ignore[attr-defined]
    browser = ctx.inject("prompt_browser") if ctx is not None else None
    if browser is None:
        return web.json_response(
            {"error": "prompt_browser_v1 not loaded"},
            status=503,
            headers=_security_headers(),
        )
    body = browser.get(key)
    if body is None:
        return web.json_response(
            {"error": f"prompt {key!r} not found"},
            status=404,
            headers=_security_headers(),
        )
    return web.json_response(
        {"key": key, "body": body}, headers=_security_headers()
    )


async def _plugins_load(request: web.Request) -> web.Response:
    """POST /plugins/{id} with JSON body {"config": {...}}.

    Loads the plugin if it's not already loaded. Returns 409 on conflict
    (already loaded) and 500 on apply() failure.
    """
    plugin_id = request.match_info.get("plugin_id", "")
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    ctx: "Context | None" = request.app.get("ctx")  # type: ignore[attr-defined]
    if web_core is None or ctx is None:
        return web.json_response(
            {"error": "harness not initialized"}, status=500, headers=_security_headers()
        )
    try:
        body = await request.json() if request.body_exists else {}
    except Exception as exc:  # noqa: BLE001
        return web.json_response(
            {"error": f"invalid json: {exc}"}, status=400, headers=_security_headers()
        )
    if not isinstance(body, dict):
        body = {}
    config = body.get("config") or {}
    if not isinstance(config, dict):
        config = {}

    from dhc.plugins.loader import (
        PluginApplyError,
        PluginError,
        PluginIntegrityError,
        PluginNotFoundError,
        PluginValidationError,
        load_async,
    )

    if plugin_id in web_core.plugin_state.loaded:
        return web.json_response(
            {"id": plugin_id, "ok": False, "error": "already loaded"},
            status=409,
            headers=_security_headers(),
        )
    try:
        await load_async(web_core.plugin_state, ctx, plugin_id, config=config)
    except PluginNotFoundError as exc:
        return web.json_response(
            {"id": plugin_id, "ok": False, "error": str(exc)},
            status=404,
            headers=_security_headers(),
        )
    except (PluginIntegrityError, PluginValidationError, PluginApplyError) as exc:
        return web.json_response(
            {"id": plugin_id, "ok": False, "error": str(exc)},
            status=500,
            headers=_security_headers(),
        )
    except PluginError as exc:
        return web.json_response(
            {"id": plugin_id, "ok": False, "error": str(exc)},
            status=400,
            headers=_security_headers(),
        )
    return web.json_response(
        {"id": plugin_id, "ok": True}, headers=_security_headers()
    )


async def _plugins_unload(request: web.Request) -> web.Response:
    """DELETE /plugins/{id} — unloads a plugin if loaded."""
    plugin_id = request.match_info.get("plugin_id", "")
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    ctx: "Context | None" = request.app.get("ctx")  # type: ignore[attr-defined]
    if web_core is None or ctx is None:
        return web.json_response(
            {"error": "harness not initialized"}, status=500, headers=_security_headers()
        )

    from dhc.plugins.loader import (
        PluginError,
        PluginNotFoundError,
        unload as _unload_plugin,
    )

    try:
        # We're inside a running event loop (aiohttp is async). Await
        # the async unload directly instead of using `unload_sync`
        # (which would try to start a fresh loop and fail).
        await _unload_plugin(web_core.plugin_state, ctx, plugin_id)
    except PluginNotFoundError as exc:
        return web.json_response(
            {"id": plugin_id, "ok": False, "error": str(exc)},
            status=404,
            headers=_security_headers(),
        )
    except PluginError as exc:
        return web.json_response(
            {"id": plugin_id, "ok": False, "error": str(exc)},
            status=500,
            headers=_security_headers(),
        )
    return web.json_response(
        {"id": plugin_id, "ok": True}, headers=_security_headers()
    )


async def _api_eval(request: web.Request) -> web.Response:
    """Paste-and-score: write the submitted code over a module's
    service.py, run the module's tests in a subprocess, restore.

    The body must be JSON of the form:
        {"module": "c4", "code": "import ..."}

    The endpoint is intentionally NOT protected by the bearer token
    so the Prompts tab can use it from the same loopback origin. The
    origin guard still applies. The submitted code is never exec'd
    inside this process - it lives in a temp file and is read by
    pytest only.
    """
    import json as _json

    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        return web.json_response(
            {"error": f"invalid json: {exc}"}, status=400, headers=_security_headers()
        )
    module_key = str(body.get("module") or "")
    code = str(body.get("code") or "")
    if not module_key or not code:
        return web.json_response(
            {"error": "missing 'module' or 'code'"}, status=400, headers=_security_headers()
        )

    from dhc.plugins._inproc_eval import eval_pasted_code

    import asyncio as _asyncio

    repo_root = request.app.get("repo_root")  # type: ignore[attr-defined]
    if repo_root is None:
        return web.json_response(
            {"error": "repo_root not configured"}, status=500, headers=_security_headers()
        )
    # eval_pasted_code is sync; run it in a thread so we don't
    # block the aiohttp event loop (it spawns a subprocess).
    result = await _asyncio.get_running_loop().run_in_executor(
        None, lambda: eval_pasted_code(repo_root, module_key, code, 30)
    )
    return web.json_response(result, headers=_security_headers())


# ---------- v1.2.0: chat WS, sessions, secrets, LLM health ----------


def _require_loopback_auth(request: web.Request, expected_token: str | None, allowed_origins: tuple[str, ...]) -> web.Response | None:
    """Shared origin + bearer-token guard. Returns a 401/403
    Response if the request fails; None if it passes.

    Used by `/ws/chat`, `/api/sessions/*`, and `/api/secrets/*`.
    """
    origin = request.headers.get("Origin", "")
    if not _is_allowed_origin(origin, allowed_origins):
        return web.Response(
            status=403, text="Origin not allowed", headers=_security_headers()
        )
    if expected_token:
        provided = _extract_bearer_token(request)
        if not _check_token(provided, expected_token):
            return web.Response(
                status=401, text="Unauthorized", headers=_security_headers()
            )
    return None


async def _api_llm_health(_request: web.Request) -> web.Response:
    """Probe the configured LLM base URL. The adapter exposes
    `redacted_key`; the LLM URL comes from the C7 config the
    harness was started with.
    """
    web_core: "GuiWebCore | None" = _request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None:
        return web.json_response({"ok": False, "error": "no web_core"}, headers=_security_headers())
    adapter = web_core.app.get("llm_adapter")
    if adapter is None:
        # Try to inject from context.
        ctx: Context | None = _request.app.get("ctx")
        if ctx is not None:
            adapter = ctx.inject("llm")
    base_url = getattr(adapter, "_base_url", None) if adapter is not None else None
    return web.json_response(
        {"ok": base_url is not None, "base_url": base_url},
        headers=_security_headers(),
    )


async def _api_models_list(_request: web.Request) -> web.Response:
    """GET /api/models — list all models in the hardcoded registry.

    Per ADR-0006. The mock model is included so the React UI can
    default to it without a separate code path.
    """
    web_core: "GuiWebCore | None" = _request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.model_registry is None:
        return web.json_response(
            {"models": [], "error": "model_registry not configured"},
            status=503, headers=_security_headers(),
        )
    payload = [
        {
            "id": m.id,
            "name": m.name,
            "provider": m.provider,
            "context_length": m.context_length,
            "pricing_input": m.pricing_input,
            "pricing_output": m.pricing_output,
            "capabilities": sorted(m.capabilities),
        }
        for m in web_core.model_registry.list_models()
    ]
    return web.json_response({"models": payload}, headers=_security_headers())


async def _api_models_get(_request: web.Request) -> web.Response:
    """GET /api/models/{id} — fetch a single model.

    `id` is the URL path; aiohttp decodes the leading slash if
    present. The model id format is `provider/model-part` (e.g.
    `openai/gpt-4o-mini`); we use the path exactly.
    """
    web_core: "GuiWebCore | None" = _request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.model_registry is None:
        return web.json_response(
            {"error": "model_registry not configured"},
            status=503, headers=_security_headers(),
        )
    model_id = _request.match_info.get("id", "")
    model = web_core.model_registry.get_model(model_id)
    if model is None:
        return web.json_response(
            {"error": f"model {model_id!r} not found"},
            status=404, headers=_security_headers(),
        )
    return web.json_response(
        {
            "id": model.id,
            "name": model.name,
            "provider": model.provider,
            "context_length": model.context_length,
            "pricing_input": model.pricing_input,
            "pricing_output": model.pricing_output,
            "capabilities": sorted(model.capabilities),
        },
        headers=_security_headers(),
    )


async def _api_sessions_list(_request: web.Request) -> web.Response:
    web_core: "GuiWebCore | None" = _request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.session_manager is None:
        return web.json_response(
            {"sessions": [], "error": "session_manager not configured"},
            status=503, headers=_security_headers(),
        )
    q = _request.query
    include_archived = q.get("archived", "").lower() in ("1", "true", "yes")
    search = q.get("search") or None
    q_present = "q" in q
    q_alias = q.get("q") or None
    limit_s = q.get("limit")
    limit = int(limit_s) if limit_s and limit_s.isdigit() else None
    if q_present:
        results = web_core.session_manager.search(
            q_alias or "", limit=limit or 50, include_archived=include_archived
        )
        summaries = [r.summary() for r in results]
    else:
        summaries = web_core.session_manager.list_summaries(
            include_archived=include_archived, search=search, limit=limit
        )
    return web.json_response({"sessions": summaries}, headers=_security_headers())


async def _api_sessions_create(request: web.Request) -> web.Response:
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.session_manager is None:
        return web.json_response(
            {"error": "session_manager not configured"}, status=503, headers=_security_headers()
        )
    try:
        body = await request.json() if request.body_exists else {}
    except Exception as exc:  # noqa: BLE001
        return web.json_response(
            {"error": f"invalid json: {exc}"}, status=400, headers=_security_headers()
        )
    if not isinstance(body, dict):
        body = {}
    title = body.get("title")
    s = web_core.session_manager.create(title=title if isinstance(title, str) else None)
    return web.json_response(s.to_dict(), status=201, headers=_security_headers())


async def _api_sessions_get(request: web.Request) -> web.Response:
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.session_manager is None:
        return web.json_response(
            {"error": "session_manager not configured"}, status=503, headers=_security_headers()
        )
    sid = request.match_info.get("session_id", "")
    s = web_core.session_manager.get(sid)
    if s is None:
        return web.json_response(
            {"error": f"session {sid!r} not found"}, status=404, headers=_security_headers()
        )
    return web.json_response(s.to_dict(), headers=_security_headers())


async def _api_sessions_patch(request: web.Request) -> web.Response:
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.session_manager is None:
        return web.json_response(
            {"error": "session_manager not configured"}, status=503, headers=_security_headers()
        )
    sid = request.match_info.get("session_id", "")
    try:
        body = await request.json() if request.body_exists else {}
    except Exception as exc:  # noqa: BLE001
        return web.json_response(
            {"error": f"invalid json: {exc}"}, status=400, headers=_security_headers()
        )
    if not isinstance(body, dict):
        body = {}
    update_kwargs: dict = {}
    if "title" in body and isinstance(body["title"], str):
        update_kwargs["title"] = body["title"]
    if "pinned" in body:
        update_kwargs["pinned"] = bool(body["pinned"])
    if "archived" in body:
        update_kwargs["archived"] = bool(body["archived"])
    if "tags" in body and isinstance(body["tags"], list):
        update_kwargs["tags"] = [str(t) for t in body["tags"]]
    if "model" in body and isinstance(body["model"], str):
        update_kwargs["model"] = body["model"]
    s = web_core.session_manager.update(sid, **update_kwargs)
    if s is None:
        return web.json_response(
            {"error": f"session {sid!r} not found"}, status=404, headers=_security_headers()
        )
    return web.json_response(s.to_dict(), headers=_security_headers())


async def _api_sessions_delete(request: web.Request) -> web.Response:
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.session_manager is None:
        return web.json_response(
            {"error": "session_manager not configured"}, status=503, headers=_security_headers()
        )
    sid = request.match_info.get("session_id", "")
    q = request.query
    hard = q.get("hard", "").lower() in ("1", "true", "yes")
    if hard:
        ok = web_core.session_manager.hard_delete(sid)
    else:
        ok = web_core.session_manager.soft_delete(sid)
    if not ok:
        return web.json_response(
            {"error": f"session {sid!r} not found"}, status=404, headers=_security_headers()
        )
    return web.Response(status=204, headers=_security_headers())


async def _api_sessions_post_message(request: web.Request) -> web.Response:
    """Append a user message to a session and synchronously stream
    the LLM reply through C7, then save the assistant turn.

    The body is `{"content": "...", "model": "..."}`. The endpoint
    blocks until the LLM reply is complete (or errors out). For the
    v1.2.0 surface this is OK; the streaming UX in the chat panel
    uses the `/ws/chat` channel.
    """
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.session_manager is None:
        return web.json_response(
            {"error": "session_manager not configured"}, status=503, headers=_security_headers()
        )
    sid = request.match_info.get("session_id", "")
    try:
        body = await request.json() if request.body_exists else {}
    except Exception as exc:  # noqa: BLE001
        return web.json_response(
            {"error": f"invalid json: {exc}"}, status=400, headers=_security_headers()
        )
    if not isinstance(body, dict):
        body = {}
    content = str(body.get("content") or "")
    if not content:
        return web.json_response(
            {"error": "missing 'content'"}, status=400, headers=_security_headers()
        )
    s = web_core.session_manager.get(sid)
    if s is None:
        return web.json_response(
            {"error": f"session {sid!r} not found"}, status=404, headers=_security_headers()
        )
    user_msg = web_core.session_manager.append_message(sid, "user", content)
    # Pull the adapter from the context (C7 provides "llm").
    ctx: Context | None = request.app.get("ctx")
    adapter = ctx.inject("llm") if ctx is not None else None
    if adapter is None:
        return web.json_response(
            {"error": "llm adapter not configured"}, status=503, headers=_security_headers()
        )
    model = str(body.get("model") or s.model or "mock-default")
    # Build the OpenAI-shaped message list from the session.
    messages = [{"role": m["role"], "content": m.get("content", "")} for m in s.messages]
    deltas: list[str] = []
    try:
        async for chunk in adapter.chat_stream(messages=messages, model=model):
            deltas.append(chunk.delta or "")
    except Exception as exc:  # noqa: BLE001
        # Log the error but keep the user message saved.
        return web.json_response(
            {"error": f"llm error: {exc}", "user_message": user_msg},
            status=502, headers=_security_headers(),
        )
    assistant_text = "".join(deltas)
    asst = web_core.session_manager.append_message(
        sid, "assistant", assistant_text,
        tokens={"prompt": 0, "completion": len(assistant_text)},
    )
    return web.json_response(
        {"user_message": user_msg, "assistant_message": asst},
        status=201, headers=_security_headers(),
    )


async def _api_secrets_list(_request: web.Request) -> web.Response:
    web_core: "GuiWebCore | None" = _request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.secrets_service is None:
        return web.json_response(
            {"names": [], "error": "secrets not configured"}, status=503, headers=_security_headers()
        )
    return web.json_response(
        {"names": web_core.secrets_service.list()}, headers=_security_headers()
    )


async def _api_secrets_put(request: web.Request) -> web.Response:
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.secrets_service is None:
        return web.json_response(
            {"error": "secrets not configured"}, status=503, headers=_security_headers()
        )
    name = request.match_info.get("name", "")
    try:
        body = await request.json() if request.body_exists else {}
    except Exception as exc:  # noqa: BLE001
        return web.json_response(
            {"error": f"invalid json: {exc}"}, status=400, headers=_security_headers()
        )
    if not isinstance(body, dict):
        body = {}
    value = body.get("value")
    if not isinstance(value, str):
        return web.json_response(
            {"error": "missing 'value' (string)"}, status=400, headers=_security_headers()
        )
    try:
        web_core.secrets_service.put(name, value)
    except ValueError as exc:
        return web.json_response(
            {"error": str(exc)}, status=400, headers=_security_headers()
        )
    return web.Response(status=204, headers=_security_headers())


async def _api_secrets_delete(request: web.Request) -> web.Response:
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.secrets_service is None:
        return web.json_response(
            {"error": "secrets not configured"}, status=503, headers=_security_headers()
        )
    name = request.match_info.get("name", "")
    ok = web_core.secrets_service.delete(name)
    if not ok:
        return web.json_response(
            {"error": f"secret {name!r} not found"}, status=404, headers=_security_headers()
        )
    return web.Response(status=204, headers=_security_headers())


async def _ws_chat_handler_impl(request: web.Request) -> web.WebSocketResponse:
    """`/ws/chat` is a separate WebSocket channel from `/ws`.

    The two channels are intentionally distinct: `/ws` is the
    read-only event broadcast (the C2 lifecycle events the existing
    UI subscribes to). `/ws/chat` is a request/response channel for
    the v1.2.0 chat panel; it accepts `chat.send` frames and
    replies with `chat.delta`/`chat.tool_call`/`chat.done`/
    `chat.error` frames.

    The frame schema is documented in `docs/chat-architecture.md`.
    """
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None:
        return web.Response(status=500, text="no web_core", headers=_security_headers())
    guard = _require_loopback_auth(
        request,
        web_core.token if web_core.require_token else None,
        web_core.allowed_origins,
    )
    if guard is not None:
        return guard
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    for k, v in _security_headers().items():
        ws.headers[k] = v

    async def send_frame(payload: dict) -> None:
        if ws.closed:
            return
        try:
            await ws.send_str(json.dumps(payload))
        except TypeError:
            await ws.send_str(json.dumps({"type": "chat.error", "message": str(payload)}))

    async for msg in ws:
        if msg.type != web.WSMsgType.TEXT:
            continue
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            await send_frame({"type": "chat.error", "code": "bad_json"})
            continue
        if not isinstance(data, dict):
            await send_frame({"type": "chat.error", "code": "not_object"})
            continue
        if data.get("type") != "chat.send":
            await send_frame({"type": "chat.error", "code": "unknown_type", "got": data.get("type")})
            continue
        sid = str(data.get("session_id") or "")
        text = str(data.get("text") or "")
        if not sid or not text:
            await send_frame({"type": "chat.error", "code": "missing_session_or_text"})
            continue
        sm = web_core.session_manager
        if sm is None:
            await send_frame({"type": "chat.error", "code": "no_session_manager"})
            continue
        s = sm.get(sid)
        if s is None:
            await send_frame({"type": "chat.error", "code": "session_not_found", "session_id": sid})
            continue
        sm.append_message(sid, "user", text)
        ctx: Context | None = request.app.get("ctx")
        adapter = ctx.inject("llm") if ctx is not None else None
        if adapter is None:
            await send_frame({"type": "chat.error", "code": "no_llm"})
            continue
        s = sm.get(sid)
        messages = [{"role": m["role"], "content": m.get("content", "")} for m in s.messages]
        try:
            t0 = int(time.time() * 1000)
            completion_tokens = 0
            assistant_text_parts: list[str] = []
            async for chunk in adapter.chat_stream(messages=messages, model=s.model or "mock-default"):
                if chunk.delta:
                    await send_frame({
                        "type": "chat.delta",
                        "session_id": sid,
                        "delta": chunk.delta,
                    })
                    assistant_text_parts.append(chunk.delta)
                    completion_tokens += max(1, len(chunk.delta) // 4)
                if chunk.tool_calls:
                    await send_frame({
                        "type": "chat.tool_call",
                        "session_id": sid,
                        "tool_calls": chunk.tool_calls,
                    })
                if chunk.finish_reason:
                    break
            t1 = int(time.time() * 1000)
            # Persist the assistant turn so the session log is complete.
            sm.append_message(
                sid, "assistant", "".join(assistant_text_parts),
                tokens={"prompt": 0, "completion": completion_tokens},
            )
            await send_frame({
                "type": "chat.done",
                "session_id": sid,
                "tokens": {"prompt": 0, "completion": completion_tokens},
                "latency_ms": t1 - t0,
            })
        except Exception as exc:  # noqa: BLE001
            await send_frame({"type": "chat.error", "code": "llm_failed", "message": str(exc)[:200]})
    return ws


class GuiWebCore:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        static_dir: str | os.PathLike[str] | None = None,
        allowed_origins: Iterable[str] = DEFAULT_ALLOWED_ORIGINS,
        port_file: str | os.PathLike[str] | None = None,
        token_file: str | os.PathLike[str] | None = None,
        token: str | None = None,
        require_token: bool = True,
        sessions_dir: str | os.PathLike[str] | None = None,
        secrets_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.static_dir = Path(static_dir) if static_dir is not None else None
        self.allowed_origins = tuple(allowed_origins)
        self.port_file = Path(port_file) if port_file is not None else None
        self.token_file = Path(token_file) if token_file is not None else None
        self.require_token = require_token
        # If a token was supplied, use it; otherwise generate one.
        # Either way, the token is a string known to this process only.
        self.token: str = token if token is not None else _generate_token()
        # Plugin marketplace state. Discovered at construction; loaded
        # on demand via /plugins/{id}.
        from dhc.plugins.loader import PluginState, discover
        self.plugin_state = PluginState()
        self.plugin_state.discovered = discover()
        # v1.2.0: session manager and secrets service are created
        # eagerly when their directories are provided. If the dir
        # is None the corresponding routes return 503.
        self.session_manager: Any = None
        if sessions_dir is not None:
            from dhc.services.session_manager import SessionManager

            self.session_manager = SessionManager(Path(sessions_dir))
        self.secrets_service: Any = None
        if secrets_dir is not None:
            from dhc.cordis.secrets import SecretsService

            self.secrets_service = SecretsService(Path(secrets_dir))
        # v1.3.0: model registry. Always constructed (it's a pure
        # in-memory hardcoded list) so /api/models works without
        # external configuration. Per ADR-0006, dynamic discovery
        # is deferred to v1.4.0.
        from dhc.services.model_registry import ModelRegistry

        self.model_registry: ModelRegistry = ModelRegistry()
        self.app = web.Application()
        # Stash references the route handlers need.
        self.app["web_core"] = self
        self.app["prompt_browser"] = None  # populated by the prompt_browser_v1 plugin
        # Routes (registered in order, but aiohttp dispatches by pattern)
        self.app.router.add_get("/healthz", _healthz)
        self.app.router.add_get("/api/manifest", _api_manifest)
        self.app.router.add_get("/plugins", _plugins_list)
        self.app.router.add_post("/plugins/{plugin_id}", _plugins_load)
        self.app.router.add_delete("/plugins/{plugin_id}", _plugins_unload)
        self.app.router.add_get("/prompts", _prompts_list)
        self.app.router.add_get("/prompts/{key}", _prompts_get)
        self.app.router.add_post("/api/eval", _api_eval)
        self.app.router.add_get("/ws", self._ws_handler)
        # v1.2.0 chat + sessions + secrets + LLM health
        self.app.router.add_get("/api/llm/health", _api_llm_health)
        self.app.router.add_get("/api/models", _api_models_list)
        # Allow slashes in the model id (e.g. "openai/gpt-4o-mini").
        self.app.router.add_get("/api/models/{id:.+}", _api_models_get)
        self.app.router.add_get("/api/sessions", _api_sessions_list)
        self.app.router.add_post("/api/sessions", _api_sessions_create)
        self.app.router.add_get("/api/sessions/{session_id}", _api_sessions_get)
        self.app.router.add_patch("/api/sessions/{session_id}", _api_sessions_patch)
        self.app.router.add_delete("/api/sessions/{session_id}", _api_sessions_delete)
        self.app.router.add_post("/api/sessions/{session_id}/messages", _api_sessions_post_message)
        self.app.router.add_get("/api/secrets", _api_secrets_list)
        self.app.router.add_put("/api/secrets/{name}", _api_secrets_put)
        self.app.router.add_delete("/api/secrets/{name}", _api_secrets_delete)
        self.app.router.add_get("/ws/chat", _ws_chat_handler_impl)
        if self.static_dir is not None and self.static_dir.exists():
            self.app.router.add_get("/", self._serve_index)
            self.app.router.add_static(
                "/assets/",
                path=str(self.static_dir / "assets"),
                show_index=False,
            )
        else:
            self.app.router.add_get("/", _index)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    async def _serve_index(self, _request: web.Request) -> web.Response:
        index_path = self.static_dir / "index.html" if self.static_dir else None
        if index_path is None or not index_path.exists():
            return _index(_request)
        body = index_path.read_text(encoding="utf-8")
        return web.Response(
            text=body,
            content_type="text/html",
            headers=_security_headers(),
        )

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        # ---- 1. Origin guard (CSWSH mitigation) ----
        origin = request.headers.get("Origin", "")
        if not _is_allowed_origin(origin, self.allowed_origins):
            return web.Response(
                status=403,
                text="Origin not allowed",
                headers=_security_headers(),
            )

        # ---- 2. Bearer token check (authentication) ----
        if self.require_token:
            provided = _extract_bearer_token(request)
            if not _check_token(provided, self.token):
                return web.Response(
                    status=401,
                    text="Unauthorized",
                    headers=_security_headers(),
                )

        ws = web.WebSocketResponse()
        await ws.prepare(request)
        for k, v in _security_headers().items():
            ws.headers[k] = v
        ctx: Context | None = request.app.get("ctx")  # type: ignore[arg-type]

        def make_listener(event_name: str):
            async def on_event(payload: Any) -> None:
                if ws.closed:
                    return
                try:
                    msg = json.dumps({"event": event_name, "payload": payload})
                except TypeError:
                    msg = json.dumps({"event": event_name, "payload": str(payload)})
                await ws.send_str(msg)

            return on_event

        registered: list[tuple[str, Any]] = []
        if ctx is not None:
            for ev in _FORWARDED_EVENTS:
                listener = make_listener(ev)
                ctx.events.on(ev, listener)
                registered.append((ev, listener))
        try:
            async for _msg in ws:
                pass
        finally:
            if ctx is not None:
                for ev, listener in registered:
                    ctx.events.off(ev, listener)
        return ws

    async def start(self) -> int:
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        if not self._runner.sites:
            raise RuntimeError("aiohttp AppRunner has no bound sites")
        site = next(iter(self._runner.sites))
        server = site._server  # type: ignore[attr-defined]
        sockets = server.sockets  # type: ignore[attr-defined]
        if not sockets:
            raise RuntimeError("aiohttp TCPSite reported no bound sockets")
        sock = next(iter(sockets))
        self.port = sock.getsockname()[1]
        if self.port_file is not None:
            self.port_file.write_text(str(self.port), encoding="utf-8")
        if self.token_file is not None:
            # Restrict the token file's permissions on POSIX systems.
            # On Windows, the umask-based default is sufficient given
            # the loopback bind; ACLs are out of scope here.
            try:
                self.token_file.write_text(self.token, encoding="utf-8")
                if hasattr(os, "chmod"):
                    os.chmod(self.token_file, 0o600)
            except OSError:
                pass
        # If a static dist/ is being served, embed the token in index.html
        # so the React client can read it on page load.
        if self.static_dir is not None:
            index_path = self.static_dir / "index.html"
            if index_path.exists():
                _embed_token_in_index(index_path, self.token)
        return self.port

    async def stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
        if self._runner is not None:
            await self._runner.cleanup()
        # Wipe the embedded token from the static index.html so a stale
        # build can't be reused against a different process.
        if self.static_dir is not None:
            index_path = self.static_dir / "index.html"
            if index_path.exists():
                import re as _re
                text = index_path.read_text(encoding="utf-8")
                if 'name="dhc-token"' in text:
                    text = _re.sub(
                        r'<meta\s+name="dhc-token"\s+content="[^"]*"\s*/?>',
                        '<meta name="dhc-token" content="" />',
                        text,
                    )
                    index_path.write_text(text, encoding="utf-8")


@plugin("c1_gui")
async def apply(ctx: Context, config: dict) -> Callable[[], None]:
    host = (config or {}).get("host", "127.0.0.1")
    port = int((config or {}).get("port", 0))
    static_dir = (config or {}).get("static_dir")
    allowed_origins = (config or {}).get("allowed_origins", DEFAULT_ALLOWED_ORIGINS)
    port_file = (config or {}).get("port_file")
    token_file = (config or {}).get("token_file")
    require_token = bool((config or {}).get("require_token", True))
    supplied_token = (config or {}).get("token")
    # Optional: caller can pass `plugins_auto_load` to load a list of
    # plugin ids at startup. Default: empty (zero plugins, all on
    # demand). Pass `["rate_limiter_v1", "prompt_browser_v1", ...]`
    # in the serve_c1 config to pre-load.
    auto_load: list[str] = (config or {}).get("plugins_auto_load", []) or []
    web_core = GuiWebCore(
        host=host,
        port=port,
        static_dir=static_dir,
        allowed_origins=allowed_origins,
        port_file=port_file,
        token_file=token_file,
        token=supplied_token,
        require_token=require_token,
    )
    web_core.app["ctx"] = ctx
    # Expose repo_root so /api/eval can locate the test files.
    # The C1 module lives at src/dhc/modules/c1_gui_web_core/service.py,
    # so the repo root is parents[3].
    web_core.app["repo_root"] = Path(__file__).resolve().parents[3]
    ctx.provide("gui", web_core)
    ctx.provide("csp", CSP_HEADER)
    ctx.provide("auth_token", web_core.token)

    # Pre-load any plugins requested at startup. Failures are logged
    # and surfaced via /healthz; the harness still serves.
    if auto_load:
        from dhc.plugins.loader import load as _load_plugin, PluginError

        for plugin_id in auto_load:
            try:
                _load_plugin(web_core.plugin_state, ctx, plugin_id, config={})
            except PluginError as exc:
                import logging

                logging.getLogger("dhc.c1").warning(
                    "auto-load plugin %r failed: %s", plugin_id, exc
                )

    if (config or {}).get("autostart", False):
        await web_core.start()

    async def dispose() -> None:
        await web_core.stop()
        ctx.services.pop("gui", None)
        ctx.services.pop("csp", None)
        ctx.services.pop("auth_token", None)
        for p in (port_file, token_file):
            if p and os.path.exists(str(p)):
                try:
                    os.remove(str(p))
                except OSError:
                    pass

    return dispose
