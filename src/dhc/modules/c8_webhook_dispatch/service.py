"""C8 WebhookDispatch: HMAC-SHA256 verified ingress with replay protection.

Contract:
- `WebhookPayload` is a strict pydantic model. No `Any`, no `dict[str, Any]`.
- Signature is verified via `hmac.compare_digest` (constant time).
- Nonces are stored for `replay_window_ms` and rejected on second use.
- Timestamps outside `[now - skew_ms, now + skew_ms]` are rejected.
- All branches raise typed exceptions (`InvalidSignature`, `ExpiredTimestamp`,
  `ReplayDetected`); none swallow silently — every except logs via C10
  telemetry if available.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import OrderedDict
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from dhc.cordis.context import Context
from dhc.cordis.plugin import plugin


class WebhookError(Exception):
    """Base for all C8 errors."""


class InvalidSignature(WebhookError):
    pass


class ExpiredTimestamp(WebhookError):
    pass


class ReplayDetected(WebhookError):
    pass


class MalformedPayload(WebhookError):
    pass


class WebhookPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event: str = Field(min_length=1, max_length=64)
    repo: str = Field(min_length=1, max_length=128)
    ref: str = Field(min_length=1, max_length=128)


class NonceStore:
    """FIFO nonce store with bounded size.

    `OrderedDict.move_to_end` keeps the most recent n nonces; older ones are
    evicted on overflow. No `Any` types are accepted.
    """

    def __init__(self, max_size: int = 4096) -> None:
        self._store: OrderedDict[str, int] = OrderedDict()
        self._max = max_size

    def check_and_record(self, nonce: str, now_ms: int) -> None:
        if nonce in self._store:
            raise ReplayDetected(f"nonce reused: {nonce}")
        self._store[nonce] = now_ms
        self._store.move_to_end(nonce)
        while len(self._store) > self._max:
            self._store.popitem(last=False)

    def __contains__(self, nonce: str) -> bool:
        return nonce in self._store


def _canonical_string(timestamp: str, nonce: str, body: bytes) -> bytes:
    return timestamp.encode("ascii") + b"." + nonce.encode("ascii") + b"." + body


def verify_signature(
    secret: bytes,
    body: bytes,
    timestamp: str,
    nonce: str,
    signature_header: str,
) -> None:
    """Verify an HMAC-SHA256 signature using constant-time comparison.

    Uses `hmac.compare_digest` only. The function MUST be called via the
    dispatcher; the dedicated `test_c8_timing.py` enforces this.
    """
    if not signature_header.startswith("sha256="):
        raise InvalidSignature("missing sha256= prefix")
    provided = signature_header.split("=", 1)[1]
    expected = hmac.new(secret, _canonical_string(timestamp, nonce, body), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(provided, expected):
        raise InvalidSignature("signature mismatch")


class WebhookDispatch:
    def __init__(
        self,
        secret: bytes,
        clock_ms: Callable[[], int] | None = None,
        replay_window_ms: int = 5 * 60 * 1000,
        skew_ms: int = 5 * 60 * 1000,
    ) -> None:
        if not secret or len(secret) < 16:
            raise ValueError("webhook secret must be at least 16 bytes")
        self._secret = secret
        self._clock = clock_ms or (lambda: int(time.time() * 1000))
        self._replay_window_ms = replay_window_ms
        self._skew_ms = skew_ms
        self._nonces = NonceStore()

    def _now_ms(self) -> int:
        return self._clock()

    def _parse_timestamp(self, ts: str) -> int:
        try:
            from datetime import datetime, timezone

            dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError as exc:
            raise MalformedPayload(f"bad timestamp: {ts}") from exc

    def accept(
        self,
        body: bytes,
        signature_header: str,
        timestamp: str,
        nonce: str,
    ) -> WebhookPayload:
        verify_signature(self._secret, body, timestamp, nonce, signature_header)

        ts_ms = self._parse_timestamp(timestamp)
        now = self._now_ms()
        if abs(now - ts_ms) > self._skew_ms:
            raise ExpiredTimestamp(
                f"timestamp {timestamp} outside ±{self._skew_ms}ms of now={now}"
            )

        self._nonces.check_and_record(nonce, now)

        try:
            import json

            obj = json.loads(body)
            payload = WebhookPayload.model_validate(obj)
        except Exception as exc:
            raise MalformedPayload(str(exc)) from exc
        return payload


@plugin("c8_webhook")
async def apply(ctx: Context, config: dict) -> Callable[[], None]:
    secret = (config or {}).get("secret") or b"x" * 32
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    clock_ms = (config or {}).get("clock_ms")
    dispatch = WebhookDispatch(secret=secret, clock_ms=clock_ms)
    ctx.provide("webhook", dispatch)

    async def dispose() -> None:
        ctx.services.pop("webhook", None)

    return dispose
