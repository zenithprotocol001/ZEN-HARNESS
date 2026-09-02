"""Tests for dhc.cordis.secrets v0x02 envelope (ADR-0010).

These tests exercise the per-secret-nonce envelope (`DHC2`)
introduced in v1.3.1. The key property: every `seal()` call
uses a unique scrypt KDF salt (the per-envelope nonce), so two
seals of the same plaintext produce ciphertexts that are
indistinguishable (different nonce + different KDF output).

Backward compatibility: v0x01 (`DHC1`) envelopes from v1.2.0 and
v1.3.0 are still readable by `open_envelope`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from dhc.cordis.secrets import (
    HEADER_V1,
    HEADER_V2,
    NONCE_LEN,
    SecretEnvelopeError,
    SecretsService,
    TAG_LEN,
    _derive_keys,
    _seal_with,
    open_envelope,
    seal,
)


MASTER = b"k" * 32


def test_seal_v2_uses_per_envelope_salt():
    """Two seals of the same plaintext produce different ciphertexts.

    The KDF salt is the per-envelope nonce, so even if the nonces
    were reused (they are not; see below), the KDF output would
    differ. In practice both the nonce and the KDF differ.
    """
    pt = b"the same plaintext"
    e1 = seal(pt, MASTER)
    e2 = seal(pt, MASTER)
    assert e1 != e2
    # Both envelopes are v0x02.
    assert e1[:4] == HEADER_V2
    assert e2[:4] == HEADER_V2


def test_open_v2_round_trip():
    """seal → open returns the original plaintext, including for
    sizes that cross the HMAC-SHA256 block boundary (32 bytes)."""
    for n in [0, 1, 15, 16, 17, 31, 32, 33, 100, 1024, 65537]:
        pt = bytes((i * 7 + 3) & 0xFF for i in range(n))
        env = seal(pt, MASTER)
        assert env[:4] == HEADER_V2
        assert open_envelope(env, MASTER) == pt


def test_open_v2_detects_ciphertext_tampering():
    """Flipping a single bit in the ciphertext must cause the MAC
    verification to fail."""
    env = seal(b"secret api key", MASTER)
    bad = bytearray(env)
    # ct occupies bytes [20:len-32]; flip something mid-ciphertext.
    bad[len(HEADER_V2) + NONCE_LEN + 3] ^= 0x10
    with pytest.raises(SecretEnvelopeError):
        open_envelope(bytes(bad), MASTER)


def test_open_v2_detects_nonce_tampering():
    """Flipping a bit in the per-envelope nonce must cause the
    KDF-derived subkeys to change, which means the MAC will
    fail. The check is end-to-end; the test asserts it."""
    env = seal(b"secret api key", MASTER)
    bad = bytearray(env)
    # Flip a bit in the nonce field (offset 4..19).
    bad[5] ^= 0x01
    with pytest.raises(SecretEnvelopeError):
        open_envelope(bytes(bad), MASTER)


def test_open_v1_still_works():
    """A v0x01 envelope (DHC1) is decryptable by the current
    `open_envelope`, with the fixed-salt KDF. This is the
    backward-compatibility requirement for v1.2.0 / v1.3.0 logs."""
    # Build a v0x01 envelope with the internal helper so we can
    # round-trip without depending on the public `seal`.
    env = _seal_with(b"legacy secret", MASTER, HEADER_V1)
    assert env[:4] == HEADER_V1
    assert open_envelope(env, MASTER) == b"legacy secret"


def test_open_unknown_header_raises():
    """Unknown headers raise `SecretEnvelopeError`; we do NOT
    fall through to the v0x01 path."""
    bad = b"DHCX" + b"\x00" * 16 + b"ciphertext" + b"\x00" * 32
    with pytest.raises(SecretEnvelopeError):
        open_envelope(bad, MASTER)


def test_seal_v2_envelope_layout():
    """Envelope bytes 0..3 are `DHC2`, 4..19 are the 16-byte
    nonce, 20..n-32 are the ciphertext, last 32 are the tag."""
    pt = b"layout check"
    env = seal(pt, MASTER)
    assert env[:4] == HEADER_V2
    # Nonce is 16 bytes; verify it's random-looking (not all zero,
    # not all the same byte).
    nonce = env[4:4 + NONCE_LEN]
    assert len(nonce) == NONCE_LEN
    assert len(set(nonce)) > 1
    # Tag is 32 bytes at the end.
    assert len(env) == 4 + NONCE_LEN + len(pt) + TAG_LEN


def test_secrets_service_round_trip_v2(tmp_path: Path):
    """End-to-end: a secret written via `SecretsService.put` is
    readable via `SecretsService.get`, and the on-disk envelope
    is `DHC2` (v0x02)."""
    svc = SecretsService(tmp_path)
    svc.put("openai_api_key", "sk-test-1234567890")
    # The on-disk log should contain a DHC2 envelope (base64).
    log_text = (tmp_path / "secrets.log").read_text(encoding="utf-8")
    assert "DHC2" not in log_text  # the on-disk form is base64, not raw
    # But the decoded envelope must start with DHC2.
    import base64, json
    rec = json.loads(log_text.strip().splitlines()[0])
    blob = base64.b64decode(rec["blob"])
    assert blob[:4] == HEADER_V2
    # And the round-trip works.
    assert svc.get("openai_api_key") == "sk-test-1234567890"
