"""WS handshake probe against the C1 server.

Reproduces the origin/token matrix the PowerShell probe attempted,
using aiohttp's ws_connect (which does speak WebSocket).
"""
import asyncio
import sys
from pathlib import Path

import aiohttp

REPO = Path(__file__).resolve().parents[1]
PORT_FILE = REPO / "serve_c1.port"
TOKEN_FILE = REPO / "serve_c1.token"


async def attempt(session, url, origin, token, label):
    headers = {
        "Origin": origin,
        "Upgrade": "websocket",
        "Connection": "Upgrade",
        "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        async with session.ws_connect(url, headers=headers, autoclose=False) as ws:
            await ws.close()
            return label + "  UPGRADED 101"
    except aiohttp.WSServerHandshakeError as e:
        return label + f"  REJECTED {e.status} ({e.message})"
    except Exception as e:
        return label + f"  ERROR {type(e).__name__}: {e}"


async def main():
    port = PORT_FILE.read_text().strip()
    token = TOKEN_FILE.read_text().strip()
    url = f"http://127.0.0.1:{port}/ws"
    print(f"target: {url}")
    print(f"token:  {token[:8]}...{token[-4:]}  (len={len(token)})")
    print()
    async with aiohttp.ClientSession() as session:
        cases = [
            (f"http://127.0.0.1:{port}", token,  "loopback + valid token  "),
            (f"http://127.0.0.1:{port}", "",      "loopback + NO token    "),
            (f"http://127.0.0.1:{port}", "wrong",  "loopback + WRONG token "),
            ("https://evil.example.com",  token, "foreign + valid token  "),
            ("http://10.0.0.1:8080",       token, "LAN + valid token      "),
            ("http://127.0.0.1.evil.com",  token, "prefix attack          "),
        ]
        for origin, tok, label in cases:
            print("  " + (await attempt(session, url, origin, tok, label)))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
