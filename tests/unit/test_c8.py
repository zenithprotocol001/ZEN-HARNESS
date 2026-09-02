import hashlib
import hmac
import json

import pytest

from dhc.fixtures.mock_llm.scripts import (
    FROZEN_EPOCH_MS,
    NONCE_SEQUENCE,
    WEBHOOK_SECRET,
    VALID_HMAC_BODY,
    VALID_HMAC_DIGEST,
    VALID_HMAC_NONCE,
    VALID_HMAC_TIMESTAMP,
)
from dhc.modules.c8_webhook_dispatch.service import (
    ExpiredTimestamp,
    InvalidSignature,
    MalformedPayload,
    ReplayDetected,
    WebhookDispatch,
    WebhookPayload,
    verify_signature,
)


def _frozen_clock(_offset_ms: int = 0):
    return lambda: FROZEN_EPOCH_MS + _offset_ms


def _build_dispatch(offset_ms: int = 0) -> WebhookDispatch:
    return WebhookDispatch(secret=WEBHOOK_SECRET, clock_ms=_frozen_clock(offset_ms))


def test_c8_happy_path():
    d = _build_dispatch()
    payload = d.accept(
        body=VALID_HMAC_BODY,
        signature_header=VALID_HMAC_DIGEST,
        timestamp=VALID_HMAC_TIMESTAMP,
        nonce=VALID_HMAC_NONCE,
    )
    assert isinstance(payload, WebhookPayload)
    assert payload.event == "push"
    assert payload.repo == "acme/widgets"


def test_c8_tampered_signature_rejected():
    from dhc.fixtures.mock_llm.scripts import TAMPERED_HMAC_DIGEST

    d = _build_dispatch()
    with pytest.raises(InvalidSignature):
        d.accept(
            body=VALID_HMAC_BODY,
            signature_header=TAMPERED_HMAC_DIGEST,
            timestamp=VALID_HMAC_TIMESTAMP,
            nonce=NONCE_SEQUENCE[0],
        )


def test_c8_old_timestamp_rejected():
    from dhc.fixtures.mock_llm.scripts import TAMPERED_HMAC_TIMESTAMP

    body = VALID_HMAC_BODY
    ts = TAMPERED_HMAC_TIMESTAMP
    nonce = NONCE_SEQUENCE[1]
    digest = "sha256=" + hmac.new(
        WEBHOOK_SECRET,
        ts.encode("ascii") + b"." + nonce.encode("ascii") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    d = _build_dispatch()
    with pytest.raises(ExpiredTimestamp):
        d.accept(body, digest, ts, nonce)


def test_c8_nonce_replay_rejected():
    d = _build_dispatch()
    nonce = NONCE_SEQUENCE[2]
    digest = "sha256=" + hmac.new(
        WEBHOOK_SECRET,
        VALID_HMAC_TIMESTAMP.encode("ascii") + b"." + nonce.encode("ascii") + b"." + VALID_HMAC_BODY,
        hashlib.sha256,
    ).hexdigest()
    d.accept(VALID_HMAC_BODY, digest, VALID_HMAC_TIMESTAMP, nonce)
    with pytest.raises(ReplayDetected):
        d.accept(VALID_HMAC_BODY, digest, VALID_HMAC_TIMESTAMP, nonce)


def test_c8_malformed_body_rejected():
    bad = b"{not json"
    ts = VALID_HMAC_TIMESTAMP
    nonce = NONCE_SEQUENCE[3]
    digest = "sha256=" + hmac.new(
        WEBHOOK_SECRET,
        ts.encode("ascii") + b"." + nonce.encode("ascii") + b"." + bad,
        hashlib.sha256,
    ).hexdigest()
    d = _build_dispatch()
    with pytest.raises(MalformedPayload):
        d.accept(bad, digest, ts, nonce)


def test_c8_payload_is_strict():
    obj = {"event": "push", "repo": "r", "ref": "refs/heads/x", "extra": "nope"}
    with pytest.raises(Exception):
        WebhookPayload.model_validate(obj)


def test_c8_verify_signature_helper():
    verify_signature(
        secret=WEBHOOK_SECRET,
        body=VALID_HMAC_BODY,
        timestamp=VALID_HMAC_TIMESTAMP,
        nonce=VALID_HMAC_NONCE,
        signature_header=VALID_HMAC_DIGEST,
    )


def test_c8_short_secret_rejected():
    with pytest.raises(ValueError):
        WebhookDispatch(secret=b"too-short", clock_ms=_frozen_clock())
