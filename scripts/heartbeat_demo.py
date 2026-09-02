"""Bounded demo: emit one `system/heartbeat` per 0.4s for 12 seconds.

Demonstrates the C2 ring buffer (NOT the immutable session log) and
the C1 WebSocket forwarder. Connects to an already-running C1 server
using its bearer token (read from `serve_c1.token`).

Run alongside the C1 server:
    PYTHONPATH=src python scripts/heartbeat_demo.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import aiohttp

from dhc.cordis.context import Context
from dhc.modules.c2_session_event_log.service import apply as apply_c2
from dhc.modules.c10_observability_sink.service import apply as apply_c10


async def main() -> int:
    port_file = ROOT / "serve_c1.port"
    token_file = ROOT / "serve_c1.token"
    if not port_file.exists() or not token_file.exists():
        print("[heartbeat] serve_c1 not running (no port/token file)", flush=True)
        return 1
    port = int(port_file.read_text(encoding="utf-8").strip())
    token = token_file.read_text(encoding="utf-8").strip()

    ctx = Context()
    await apply_c2(ctx, {"heartbeat_ring_size": 16})
    await apply_c10(ctx)

    # Do NOT start a second C1; we just need the Cordis context to emit
    # events. We'll connect to the running C1's WS to verify.
    url = "http://127.0.0.1:{0}/ws".format(port)
    headers = {"Origin": "http://127.0.0.1:{0}".format(port), "Authorization": "Bearer " + token}
    print("[heartbeat] connecting to {0}".format(url), flush=True)

    async with aiohttp.ClientSession() as session:
        try:
            async with session.ws_connect(url, headers=headers, autoclose=False) as ws:
                print("[heartbeat] connected. emitting 30 events @ 0.4s", flush=True)
                for i in range(30):
                    await ctx.events.emit(
                        "system/heartbeat",
                        {
                            "ts_ms": int(time.time() * 1000),
                            "source": "heartbeat_demo",
                            "detail": {"seq": i, "msg": "tick {0}".format(i)},
                        },
                    )
                    print(
                        "[heartbeat] tick {0:2d}  session_log={1}  ring={2}".format(
                            i, len(ctx.inject("sessions")), len(ctx.inject("sessions").get_heartbeats())
                        ),
                        flush=True,
                    )
                    await asyncio.sleep(0.4)
                await ws.close()
        except aiohttp.WSServerHandshakeError as e:
            print("[heartbeat] WS rejected: {0} {1}".format(e.status, e.message), flush=True)
            return 2

    log = ctx.inject("sessions")
    print("[heartbeat] done. session log size = {0}  (expected: 0)".format(len(log)), flush=True)
    print("[heartbeat] heartbeats in ring: {0}  (capped at 16)".format(len(log.get_heartbeats())), flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
