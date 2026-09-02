"""End-to-end live demo: spin up a C1 server in-process, drive a
3-step turn, and dump every WS event the React UI would see.

This is the same plumbing the React UI uses under the hood:
  - C1 GuiWebCore binds to an ephemeral port
  - WS client connects with the loopback origin and the bearer token
  - We emit Cordis events directly into the SAME Context
  - We print every event the UI would render
"""
import asyncio
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import aiohttp

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


# ANSI colors
GREEN, CYAN, YELLOW, RED, MAGENTA, DIM, RESET = (
    "\033[92m", "\033[96m", "\033[93m", "\033[91m", "\033[95m", "\033[2m", "\033[0m",
)


def render_event(ev: dict) -> str:
    name = ev.get("event", "?")
    payload = ev.get("payload", {})
    if name == "llm/stream":
        return (
            f"  {YELLOW}[{name}]{RESET} "
            f"content={payload.get('content', '')!r}  "
            f"tool_calls={payload.get('tool_calls', [])!r}"
        )
    if name == "tool/call":
        return (
            f"  {RED}[{name}]{RESET} "
            f"tool={payload.get('tool_name')!r} args={payload.get('args')!r} "
            f"result={payload.get('result', payload.get('error'))!r}"
        )
    if name == "turn/end":
        return (
            f"  {GREEN}[{name}]{RESET} "
            f"reason={payload.get('reason')!r} steps={payload.get('steps')}"
        )
    if name == "system/heartbeat":
        return (
            f"  {MAGENTA}[{name}]{RESET} "
            f"source={payload.get('source')!r} detail={payload.get('detail')!r}"
        )
    if name in ("step/start", "step/end", "turn/start"):
        return f"  {CYAN}[{name}]{RESET} {payload!r}"
    return f"  [{name}] {payload!r}"


async def main():
    # 1. Build the same Context the UI is subscribed to.
    ctx = Context()
    await apply_c2(ctx, {"heartbeat_ring_size": 8})
    await apply_c10(ctx)
    await apply_c7(ctx, {"base_url": "http://127.0.0.1:0", "api_key": "sk-demo-key-not-real"})
    await apply_c8(ctx, {"secret": b"x" * 32})
    await apply_c4(ctx)
    await apply_c9(ctx)
    await apply_c5(ctx, {"root_secret": b"x" * 32})
    await apply_c3(ctx)
    await apply_c6(ctx, {"max_steps": 5})
    # Bind to ephemeral port (0) and autostart so we get a real port.
    await apply_c1(
        ctx,
        {"host": "127.0.0.1", "port": 0, "autostart": True, "static_dir": None, "token_file": None},
    )

    web_core = ctx.inject("gui")
    port = web_core.port
    token = ctx.inject("auth_token")
    url = f"http://127.0.0.1:{port}/ws"

    print("=" * 72)
    print(f" DHC LIVE  |  http://127.0.0.1:{port}/")
    print(f"           |  ws://127.0.0.1:{port}/ws")
    print(f"           |  token {token[:8]}...{token[-4:]}  (len={len(token)})")
    print("=" * 72)
    print()

    # 2. Open a WS client (same as the React app).
    headers = {
        "Origin": f"http://127.0.0.1:{port}",
        "Authorization": "Bearer " + token,
    }
    received: list[dict] = []
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url, headers=headers, autoclose=False) as ws:

            async def listen():
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        received.append(json.loads(msg.data))
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        break

            consumer = asyncio.create_task(listen())
            await asyncio.sleep(0.4)  # let the WS fully establish

            # 3. Drive a scripted turn that exercises every module.
            print(f"  {DIM}driving scripted turn...{RESET}")

            await ctx.events.emit("turn/start", {"agent_id": "demo-agent", "turn_id": "turn-live-1"})
            await asyncio.sleep(0.1)

            # Heartbeat (touches C2 ring + C10 sink)
            await ctx.events.emit(
                "system/heartbeat",
                {"ts_ms": int(time.time() * 1000), "source": "demo", "detail": {"msg": "live"}},
            )

            # Step 0
            await ctx.events.waterfall("agent/pre-step", {"agent_id": "demo-agent"})
            await ctx.events.emit("step/start", {"agent_id": "demo-agent", "turn_id": "turn-live-1", "step": 0})

            # llm/stream: a tool call to read_file
            await ctx.events.emit(
                "llm/stream",
                {
                    "agent_id": "demo-agent",
                    "turn_id": "turn-live-1",
                    "step": 0,
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": '{"path": "README.md"}'},
                        }
                    ],
                    "finish_reason": "tool_calls",
                },
            )
            # Grant the capability BEFORE emitting tools/pre-execute.
            ctx.inject("policy").grant("demo-agent", "read_file")
            await ctx.events.emit("tools/pre-execute", {"agent_id": "demo-agent", "tool_name": "read_file"})
            await ctx.events.emit(
                "tool/call",
                {
                    "agent_id": "demo-agent",
                    "turn_id": "turn-live-1",
                    "tool_name": "read_file",
                    "args": {"path": "README.md"},
                    "result": "Content of README.md",
                },
            )
            await ctx.events.emit("step/end", {"agent_id": "demo-agent", "turn_id": "turn-live-1", "step": 0})
            await asyncio.sleep(0.1)

            # Step 1
            await ctx.events.emit("step/start", {"agent_id": "demo-agent", "turn_id": "turn-live-1", "step": 1})
            await ctx.events.emit(
                "llm/stream",
                {
                    "agent_id": "demo-agent",
                    "turn_id": "turn-live-1",
                    "step": 1,
                    "content": "Welcome to the DHC harness. The benchmark is live.",
                    "tool_calls": [],
                    "finish_reason": "stop",
                },
            )
            await ctx.events.emit("step/end", {"agent_id": "demo-agent", "turn_id": "turn-live-1", "step": 1})
            await asyncio.sleep(0.1)

            # turn/end
            await ctx.events.emit(
                "turn/end",
                {
                    "agent_id": "demo-agent",
                    "turn_id": "turn-live-1",
                    "reason": "completed",
                    "steps": 2,
                },
            )

            await asyncio.sleep(0.6)  # let the consumer drain
            consumer.cancel()
            try:
                await consumer
            except asyncio.CancelledError:
                pass

    # 4. Print the events the React UI would render.
    print(f"\n  {GREEN}[WS]{RESET} received {len(received)} event(s) from /ws\n")
    for ev in received:
        print(render_event(ev))

    # 5. Show internal state — what C2 / C10 captured
    log = ctx.inject("sessions")
    sink = ctx.inject("telemetry")
    print()
    print("=" * 72)
    print(" Internal state captured by the in-process modules")
    print("=" * 72)
    print(f"  C2 SessionEventLog.get_history()  ->  {len(log.get_history())} events")
    for ev in log.get_history():
        print(f"    - {ev.type:14s}  {ev.id}")
    print(f"  C2 Heartbeat ring                 ->  {len(log.get_heartbeats())} heartbeats (cap=8)")
    print(f"  C10 ObservabilitySink.logs        ->  {len(sink.logs)} events")
    print()

    await web_core.stop()
    await ctx.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
