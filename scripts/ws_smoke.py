"""Connect to the C1 WebSocket via aiohttp (already installed) and listen."""

import asyncio
import sys

import aiohttp


async def main() -> int:
    url = "http://127.0.0.1:3081/ws"
    print(f"connecting to {url} ...", flush=True)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url, autoclose=False) as ws:
                print("connected. Listening for events for 3s ...", flush=True)
                try:
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            print(f"got TEXT: {msg.data[:200]}", flush=True)
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            print(f"error: {ws.exception()}", flush=True)
                            break
                except asyncio.TimeoutError:
                    pass
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(asyncio.wait_for(main(), timeout=8.0)))
    except asyncio.TimeoutError:
        print("smoke test finished (timeout reached)", flush=True)
        sys.exit(0)
