"""C1 GuiWebCore — local serve entry point (certified).

Wires all 10 Cordis plugins into a single Context, starts the C1 aiohttp
server on a loopback bind (default: 127.0.0.1, port 0 = OS-assigned),
and serves the production React client build from `apps/web/dist/` if
present. A per-launch bearer token is generated, written to
`serve_c1.token`, and embedded in the served index.html. The React
client reads the token from a `<meta>` tag and sends it on the WS
handshake as a `?token=` query parameter.

No Vite dev server. No rogue heartbeats. The only HTTP/WS server is
the Python bridge. The React build is a hermetic static artifact;
once `npm run build` is done, no further Node.js is required to
serve or run the UI.

Usage:
    python -m dhc.serve_c1
    python -m dhc.serve_c1 --host 127.0.0.1 --port 0
    python -m dhc.serve_c1 --port-file /tmp/dhc.port --token-file /tmp/dhc.token
    python -m dhc.serve_c1 --no-auth   # for trusted local dev only
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path
from typing import Any

from dhc.cordis.context import Context

from dhc.modules.c1_gui_web_core.service import apply as apply_c1
from dhc.modules.c2_session_event_log.service import apply as apply_c2
from dhc.modules.c3_prompt_assembler.service import apply as apply_c3
from dhc.modules.c4_tool_guard_pipeline.service import apply as apply_c4
from dhc.modules.c5_agent_registry.service import apply as apply_c5
from dhc.modules.c6_turn_step_driver.service import apply as apply_c6
from dhc.modules.c7_llm_stream_adapter.service import apply as apply_c7
from dhc.modules.c8_webhook_dispatch.service import apply as apply_c8
from dhc.modules.c9_capability_policy.service import apply as apply_c9
from dhc.modules.c10_observability_sink.service import apply as apply_c10


PLUGINS = [
    ("c2_session_event_log", apply_c2),
    ("c10_observability_sink", apply_c10),
    ("c7_llm_stream_adapter", apply_c7),
    ("c8_webhook_dispatch", apply_c8),
    ("c4_tool_guard_pipeline", apply_c4),
    ("c9_capability_policy", apply_c9),
    ("c5_agent_registry", apply_c5),
    ("c3_prompt_assembler", apply_c3),
    ("c6_turn_step_driver", apply_c6),
    ("c1_gui_web_core", apply_c1),
]


def _resolve_static_dir(repo_root: Path, override: str | None) -> Path | None:
    if override:
        return Path(override)
    candidate = repo_root / "apps" / "web" / "dist"
    return candidate if candidate.is_dir() else None


async def build_context(
    host: str,
    port: int,
    autostart: bool,
    static_dir: Path | None,
    port_file: Path | None,
    token_file: Path | None,
    require_token: bool,
    llm_base_url: str | None = None,
    llm_api_key: str = "",
    sessions_dir: Path | None = None,
    secrets_dir: Path | None = None,
) -> Context:
    ctx = Context()
    # v1.3.0: model registry is pure-Python; construct once and share
    # between C1 (for /api/models routes) and C7 (for live dispatch).
    from dhc.services.model_registry import ModelRegistry

    model_registry = ModelRegistry()
    # v1.3.0: secrets service is needed by C7 to look up API keys.
    # Construct it eagerly here (it's cheap) so C7 can be wired
    # at apply() time. If the user disables --secrets-dir, this
    # stays as None and C7 falls through to the v1.2.x mock path.
    secrets_service: Any = None
    config_store: Any = None
    if secrets_dir is not None:
        from dhc.cordis.secrets import SecretsService
        from dhc.services.model_config import ModelConfigStore

        secrets_dir.mkdir(parents=True, exist_ok=True)
        secrets_service = SecretsService(secrets_dir)
        # v1.3.1 (ADR-0011): per-session model config store, shared
        # with the C1 routes (model_config_store on web_core).
        config_store = ModelConfigStore(secrets_service)

    for name, fn in PLUGINS:
        cfg: dict[str, Any] = {}
        if name == "c1_gui_web_core":
            cfg = {
                "host": host,
                "port": port,
                "autostart": autostart,
                "static_dir": str(static_dir) if static_dir is not None else None,
                "port_file": str(port_file) if port_file is not None else None,
                "token_file": str(token_file) if token_file is not None else None,
                "require_token": require_token,
                "sessions_dir": str(sessions_dir) if sessions_dir is not None else None,
                "secrets_dir": str(secrets_dir) if secrets_dir is not None else None,
                "model_registry": model_registry,
            }
        elif name == "c6_turn_step_driver":
            cfg = {"max_steps": 5}
        elif name == "c7_llm_stream_adapter" and llm_base_url is not None:
            cfg = {
                "base_url": llm_base_url,
                "api_key": llm_api_key,
                "model_registry": model_registry,
                "secrets_service": secrets_service,
                "config_store": config_store,
            }
        await fn(ctx, cfg)
    return ctx


def install_signal_handlers(loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event) -> None:
    def _request_stop(signum: int, _frame: Any) -> None:
        try:
            loop.call_soon_threadsafe(stop_event.set)
        except RuntimeError:
            pass
        print("[serve_c1] signal {0} received; stopping".format(signum), flush=True)

    if sys.platform == "win32":
        signal.signal(signal.SIGINT, _request_stop)
        signal.signal(signal.SIGBREAK, _request_stop)
    else:
        signal.signal(signal.SIGINT, _request_stop)
        signal.signal(signal.SIGTERM, _request_stop)


async def run(
    host: str,
    port: int,
    port_file: Path,
    token_file: Path,
    static_dir: Path | None,
    require_token: bool,
    llm_base_url: str | None = None,
    llm_api_key: str = "",
    sessions_dir: Path | None = None,
    secrets_dir: Path | None = None,
) -> int:
    ctx = await build_context(
        host=host,
        port=port,
        autostart=True,
        static_dir=static_dir,
        port_file=port_file,
        token_file=token_file,
        require_token=require_token,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        sessions_dir=sessions_dir,
        secrets_dir=secrets_dir,
    )
    web_core = ctx.inject("gui")
    if web_core is None:
        print("[serve_c1] ERROR: C1 plugin did not register the 'gui' service", file=sys.stderr)
        await ctx.dispose()
        return 2

    bound_port = web_core.port
    csp = ctx.inject("csp")
    static_served = "yes" if static_dir is not None else "no (no apps/web/dist found)"
    auth_state = "ON (token in {0})".format(token_file) if require_token else "OFF"

    print("=" * 72, flush=True)
    print(" DHC C1 GuiWebCore (certified, token auth)", flush=True)
    print("   http:    http://{0}:{1}/".format(host, bound_port), flush=True)
    print("   ws:      ws://{0}:{1}/ws".format(host, bound_port), flush=True)
    print("   health:  http://{0}:{1}/healthz".format(host, bound_port), flush=True)
    print("   static:  {0}".format(static_served), flush=True)
    print("   port:    {0}".format(bound_port), flush=True)
    print("   auth:    {0}".format(auth_state), flush=True)
    print("   csp:     {0}".format(csp[:60] + "..."), flush=True)
    print("=" * 72, flush=True)
    print(" Press Ctrl+C to stop.", flush=True)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    install_signal_handlers(loop, stop_event)
    try:
        await stop_event.wait()
    finally:
        print("[serve_c1] disposing context...", flush=True)
        await ctx.dispose()
        print("[serve_c1] stopped.", flush=True)
    return 0


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Run the DHC C1 GuiWebCore locally.")
    parser.add_argument("--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=0,
                        help="bind port (0 = OS-assigned ephemeral; default: 0)")
    parser.add_argument("--port-file", default=str(repo_root / "serve_c1.port"),
                        help="write the bound port to this file for discovery")
    parser.add_argument("--token-file", default=str(repo_root / "serve_c1.token"),
                        help="write the per-launch bearer token to this file")
    parser.add_argument("--static-dir", default=None,
                        help="override the static dist/ directory (default: apps/web/dist)")
    parser.add_argument("--no-auth", action="store_true",
                        help="disable the bearer token requirement (trusted local dev only)")
    parser.add_argument("--llm-base-url", default=None,
                        help="base URL of the LLM server (e.g. http://127.0.0.1:3099); "
                             "if omitted, C7 has no adapter and /ws/chat will error")
    parser.add_argument("--llm-api-key", default="",
                        help="API key for the LLM server (sent in Authorization: Bearer)")
    parser.add_argument("--sessions-dir", default=str(repo_root / ".dhc" / "sessions"),
                        help="directory for chat session storage")
    parser.add_argument("--secrets-dir", default=str(repo_root / ".dhc" / "secrets"),
                        help="directory for encrypted API key storage")
    args = parser.parse_args()
    static_dir = _resolve_static_dir(repo_root, args.static_dir)
    return asyncio.run(
        run(
            args.host,
            args.port,
            Path(args.port_file),
            Path(args.token_file),
            static_dir,
            require_token=not args.no_auth,
            llm_base_url=args.llm_base_url,
            llm_api_key=args.llm_api_key,
            sessions_dir=Path(args.sessions_dir),
            secrets_dir=Path(args.secrets_dir),
        )
    )


if __name__ == "__main__":
    sys.exit(main())
