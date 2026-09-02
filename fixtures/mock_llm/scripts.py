"""Scripted fixture data for the mock LLM and the C8 webhook tamper test.

All timestamps are frozen to `2026-01-01T00:00:00Z` per the deterministic-mock
directive. Nonces are drawn from a fixed sequence, never `os.urandom`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import json
from typing import Any, Iterable

FROZEN_TIMESTAMP = "2026-01-01T00:00:00Z"
FROZEN_EPOCH_MS = 1767225600000  # 2026-01-01T00:00:00Z in ms (NOT 2025)
WEBHOOK_SECRET = b"dhc-test-secret-do-not-use-in-prod"
NONCE_SEQUENCE = [f"nonce-{i:08d}" for i in range(64)]

VALID_HMAC_NONCE: str = NONCE_SEQUENCE[7]
VALID_HMAC_TIMESTAMP: str = FROZEN_TIMESTAMP
VALID_HMAC_BODY: bytes = b'{"event":"push","repo":"acme/widgets","ref":"refs/heads/main"}'


def _canonical_string(timestamp: str, nonce: str, body: bytes) -> bytes:
    """Mirror of `dhc.modules.c8_webhook_dispatch.service._canonical_string`."""
    return timestamp.encode("ascii") + b"." + nonce.encode("ascii") + b"." + body


def _sign(timestamp: str, nonce: str, body: bytes) -> str:
    return (
        "sha256="
        + _hmac.new(WEBHOOK_SECRET, _canonical_string(timestamp, nonce, body), hashlib.sha256).hexdigest()
    )


VALID_HMAC_DIGEST: str = _sign(VALID_HMAC_TIMESTAMP, VALID_HMAC_NONCE, VALID_HMAC_BODY)


def _make_tampered_digest() -> str:
    original = bytes.fromhex(VALID_HMAC_DIGEST.split("=", 1)[1])
    flipped = bytearray(original)
    flipped[0] ^= 0x01
    return "sha256=" + flipped.hex()


TAMPERED_HMAC_DIGEST: str = _make_tampered_digest()
TAMPERED_HMAC_BODY: bytes = VALID_HMAC_BODY + b" "
TAMPERED_HMAC_TIMESTAMP: str = "2025-12-31T23:50:00Z"  # 10 minutes before frozen time — outside ±5 min window


HAPPY_3_STEP: list[dict[str, Any]] = [
    {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "choices": [
            {"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}
        ],
    },
    {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "content": "",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_a1",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": '{"path": "README.md"}'},
                        }
                    ],
                },
                "finish_reason": None,
            }
        ],
    },
    {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
    },
    {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "choices": [
            {
                "index": 0,
                "delta": {"content": "The README contains installation instructions."},
                "finish_reason": None,
            }
        ],
    },
    {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    },
]


INFINITE_NEED_MORE_INFO: list[dict[str, Any]] = [
    {
        "id": "chatcmpl-loop",
        "object": "chat.completion.chunk",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_loop",
                            "type": "function",
                            "function": {
                                "name": "ask_clarification",
                                "arguments": '{"status": "need_more_info"}',
                            },
                        }
                    ]
                },
                "finish_reason": "tool_calls",
            }
        ],
    }
]


def _json_to_sse_bytes(events: Iterable[dict[str, Any]]) -> bytes:
    out = bytearray()
    for ev in events:
        out.extend(b"data: " + json.dumps(ev, separators=(",", ":")).encode("utf-8") + b"\n\n")
    out.extend(b"data: [DONE]\n\n")
    return bytes(out)


def _fragment_bytes(payload: bytes, splits: list[int]) -> list[bytes]:
    out: list[bytes] = []
    cursor = 0
    for s in splits:
        s = min(s, len(payload))
        out.append(payload[cursor:s])
        cursor = s
    if cursor < len(payload):
        out.append(payload[cursor:])
    return out


def make_happy_bytes() -> bytes:
    return _json_to_sse_bytes(HAPPY_3_STEP)


def make_infinite_bytes() -> bytes:
    payload = _json_to_sse_bytes(INFINITE_NEED_MORE_INFO)
    return payload


FRAGMENTED_CHUNKS: list[bytes] = _fragment_bytes(
    make_happy_bytes(), splits=[1, 5, 13, 29, 60, 120, 240]
)
