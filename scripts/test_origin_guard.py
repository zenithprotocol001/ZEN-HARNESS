"""Verify the WS Origin Guard AND the bearer token auth:

  - aiohttp client with a loopback origin + valid token -> upgrade succeeds
  - aiohttp client with a foreign origin                  -> 403
  - aiohttp client with a loopback origin + bad token     -> 401
  - aiohttp client with no token at all                    -> 401

Reads the bound port from serve_c1.port and the bearer token from
serve_c1.token (both produced by `python -m dhc.serve_c1`).
"""

import asyncio
import sys
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


async def attempt(url: str, origin: str, headers: dict | None) -> str:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                url,
                autoclose=False,
                headers={"Origin": origin, **(headers or {})},
            ) as ws:
                await ws.close()
                return "UPGRADED (ok)"
    except aiohttp.WSServerHandshakeError as e:
        return "REJECTED ({0})".format(e.status)
    except Exception as e:
        return "ERROR ({0}: {1})".format(type(e).__name__, e)


async def main() -> int:
    port_file = ROOT / "serve_c1.port"
    token_file = ROOT / "serve_c1.token"
    port = int(port_file.read_text(encoding="utf-8").strip())
    token = token_file.read_text(encoding="utf-8").strip()
    url = "http://127.0.0.1:{0}/ws".format(port)
    print("target: {0}".format(url), flush=True)
    print("token:  {0}...{1}  ({2} chars)".format(token[:6], token[-4:], len(token)), flush=True)

    cases = [
        ("http://127.0.0.1:{0}".format(port), {"Authorization": "Bearer " + token}),
        ("http://127.0.0.1:{0}".format(port), {}),  # no token
        ("http://127.0.0.1:{0}".format(port), {"Authorization": "Bearer wrong-token-1234"}),
        ("https://evil.example.com", {"Authorization": "Bearer " + token}),
        ("http://attacker.com", {"Authorization": "Bearer " + token}),
        ("", {"Authorization": "Bearer " + token}),  # empty origin
    ]
    for origin, headers in cases:
        result = await attempt(url, origin, headers)
        print("  {0:40s} -> {1}".format(repr(origin), result), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
