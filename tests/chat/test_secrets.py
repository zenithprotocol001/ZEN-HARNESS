"""Tests for dhc.cordis.secrets — at-rest secret envelope + service."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from dhc.cordis.secrets import (
    DEFAULT_KEY_PATH,
    HEADER,
    HEADER_V1,
    HEADER_V2,
    NONCE_LEN,
    SecretEnvelopeError,
    SecretsService,
    TAG_LEN,
    _derive_keys,
    _keystream,
    load_or_create_master_key,
    open_envelope,
    seal,
)


# ---------- Envelope primitives ----------


def test_seal_open_roundtrip_various_sizes():
    key = b"k" * 32
    for n in [0, 1, 15, 16, 31, 32, 33, 100, 1024, 65536]:
        pt = bytes(range(256)) * (n // 256 + 1)
        pt = pt[:n]
        env = seal(pt, key)
        assert env[: len(HEADER)] in (HEADER_V1, HEADER_V2)
        assert len(env) == len(HEADER) + NONCE_LEN + n + TAG_LEN
        out = open_envelope(env, key)
        assert out == pt


def test_seal_produces_different_nonces_for_same_plaintext():
    key = b"k" * 32
    pt = b"hello world"
    e1 = seal(pt, key)
    e2 = seal(pt, key)
    # Nonces are random 16-byte values; with overwhelming probability
    # two consecutive calls will differ.
    assert e1 != e2
    # The nonces occupy bytes [len(HEADER):len(HEADER)+NONCE_LEN].
    assert e1[len(HEADER) : len(HEADER) + NONCE_LEN] != e2[
        len(HEADER) : len(HEADER) + NONCE_LEN
    ]


def test_seal_tamper_detection():
    key = b"k" * 32
    env = seal(b"the quick brown fox", key)
    # Flip one bit in the ciphertext region.
    bad = bytearray(env)
    bad[len(HEADER) + NONCE_LEN + 1] ^= 0x10
    with pytest.raises(SecretEnvelopeError):
        open_envelope(bytes(bad), key)


def test_seal_tamper_tag_detection():
    key = b"k" * 32
    env = seal(b"hello", key)
    bad = bytearray(env)
    bad[-1] ^= 0x01
    with pytest.raises(SecretEnvelopeError):
        open_envelope(bytes(bad), key)


def test_seal_tamper_header_detection():
    key = b"k" * 32
    env = seal(b"hello", key)
    bad = bytearray(env)
    bad[0] ^= 0x01
    with pytest.raises(SecretEnvelopeError):
        open_envelope(bytes(bad), key)


def test_seal_tamper_nonce_detection():
    """Flipping a nonce byte should not affect the tag, but the
    resulting decrypted plaintext should differ from the original
    (the keystream depends on the nonce). The tag is computed over
    (nonce || ct), so a tampered nonce produces a different ct and
    a different tag — tag mismatch fails open_envelope.
    """
    key = b"k" * 32
    env = seal(b"hello", key)
    bad = bytearray(env)
    bad[len(HEADER)] ^= 0x01
    with pytest.raises(SecretEnvelopeError):
        open_envelope(bytes(bad), key)


def test_seal_wrong_key_rejected():
    env = seal(b"hello", b"k" * 32)
    with pytest.raises(SecretEnvelopeError):
        open_envelope(env, b"j" * 32)


def test_seal_rejects_short_key():
    with pytest.raises(ValueError):
        seal(b"hello", b"short")
    with pytest.raises(ValueError):
        open_envelope(b"DHC1" + b"\x00" * 16 + b"x" * 5 + b"\x00" * 32, b"short")


def test_open_envelope_short_envelope():
    with pytest.raises(SecretEnvelopeError):
        open_envelope(b"too short", b"k" * 32)


def test_keystream_distinct_blocks():
    """Each HMAC-SHA256 counter-mode block should differ."""
    key = b"k" * 32
    nonce = b"n" * 16
    a = _keystream(key, nonce, 32)
    b = _keystream(key, nonce, 64)
    # First 32 bytes of (b) should equal a (same nonce, same counter start).
    assert a == b[:32]
    # But the next 32 should differ (counter 1 vs counter 0).
    # Build a longer keystream and check the two blocks are distinct.
    long_ks = _keystream(key, nonce, 128)
    assert long_ks[:32] != long_ks[32:64]


def test_derive_keys_distinct_labels():
    master = b"m" * 32
    salt = b"s" * 16
    ks_key, mac_key = _derive_keys(master, salt)
    assert len(ks_key) == 32
    assert len(mac_key) == 32
    assert ks_key != mac_key


def test_derive_keys_rejects_short_master():
    with pytest.raises(ValueError):
        _derive_keys(b"short", b"s" * 16)


# ---------- Key file management ----------


def test_load_or_create_master_key_first_run(tmp_path: Path):
    key_path = tmp_path / "secrets.key"
    key = load_or_create_master_key(key_path)
    assert len(key) == 32
    assert key_path.exists()
    # The second call should return the same bytes.
    key2 = load_or_create_master_key(key_path)
    assert key2 == key


def test_load_or_create_master_key_corrupted(tmp_path: Path):
    key_path = tmp_path / "secrets.key"
    key_path.write_bytes(b"too-short")
    with pytest.raises(SecretEnvelopeError):
        load_or_create_master_key(key_path)


# ---------- SecretsService ----------


def test_service_put_get_delete(tmp_path: Path):
    svc = SecretsService(tmp_path)
    assert svc.list() == []
    assert svc.get("openai") is None
    svc.put("openai", "sk-test-12345")
    assert svc.get("openai") == "sk-test-12345"
    assert "openai" in svc.list()
    assert svc.delete("openai") is True
    assert svc.get("openai") is None
    assert "openai" not in svc.list()
    # Deleting a missing secret returns False.
    assert svc.delete("openai") is False
    # Deleting a secret, then re-adding it, then deleting it again
    # should still work.
    svc.put("openai", "sk-test-67890")
    assert svc.get("openai") == "sk-test-67890"
    assert svc.delete("openai") is True
    assert svc.get("openai") is None


def test_service_multiple_secrets(tmp_path: Path):
    svc = SecretsService(tmp_path)
    svc.put("openai", "sk-1")
    svc.put("anthropic", "sk-2")
    svc.put("zhipu", "zhipu_3")
    assert svc.list() == ["anthropic", "openai", "zhipu"]
    assert svc.get("anthropic") == "sk-2"
    svc.delete("anthropic")
    assert svc.list() == ["openai", "zhipu"]


def test_service_log_persists_across_restart(tmp_path: Path):
    svc = SecretsService(tmp_path)
    svc.put("alpha", "value-a")
    svc.put("beta", "value-b")
    # A new service instance reads the same log.
    svc2 = SecretsService(tmp_path)
    assert svc2.get("alpha") == "value-a"
    assert svc2.get("beta") == "value-b"
    assert svc2.list() == ["alpha", "beta"]


def test_service_log_rewrite_after_delete(tmp_path: Path):
    svc = SecretsService(tmp_path)
    svc.put("k", "v1")
    svc.delete("k")
    svc.put("k", "v2")
    svc2 = SecretsService(tmp_path)
    assert svc2.get("k") == "v2"


def test_service_log_value_is_encrypted_on_disk(tmp_path: Path):
    """The plaintext must not appear in the log file. Only base64'd
    envelope bytes should be present.
    """
    svc = SecretsService(tmp_path)
    secret = "sk-supersecret-do-not-leak-1234567890"
    svc.put("openai", secret)
    log_text = (tmp_path / "secrets.log").read_text(encoding="utf-8")
    assert secret not in log_text
    # The blob field is present and is valid base64.
    record = json.loads(log_text.strip().splitlines()[0])
    assert record["op"] == "set"
    assert record["name"] == "openai"
    import base64
    blob = base64.b64decode(record["blob"])
    assert blob[: len(HEADER)] in (HEADER_V1, HEADER_V2)
    assert len(blob) >= len(HEADER) + NONCE_LEN + TAG_LEN


def test_service_list_hides_values(tmp_path: Path):
    """`list()` returns only names; values never appear in the list
    surface. This is a defensive test for the public API.
    """
    svc = SecretsService(tmp_path)
    svc.put("k1", "value-1")
    svc.put("k2", "value-2")
    names = svc.list()
    assert all(isinstance(n, str) for n in names)
    assert "value-1" not in names
    assert "value-2" not in names


def test_service_put_validation(tmp_path: Path):
    svc = SecretsService(tmp_path)
    with pytest.raises(ValueError):
        svc.put("", "value")
    # Empty value is allowed (the user may want to store an
    # "empty" key for testing the read path).
    svc.put("name", "")
    assert svc.get("name") == ""
    with pytest.raises(ValueError):
        svc.put("name", 12345)  # type: ignore[arg-type]


def test_service_concurrent_puts(tmp_path: Path):
    """Two threads putting different secrets concurrently should not
    corrupt the log.
    """
    svc = SecretsService(tmp_path)
    errors: list[Exception] = []

    def putter(prefix: str, count: int) -> None:
        try:
            for i in range(count):
                svc.put(f"{prefix}-{i}", f"value-{i}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=putter, args=("a", 20))
    t2 = threading.Thread(target=putter, args=("b", 20))
    t1.start(); t2.start()
    t1.join(); t2.join()
    assert not errors
    names = svc.list()
    assert len(names) == 40


def test_service_atomic_key_creation(tmp_path: Path):
    """load_or_create_master_key writes via a tmp + rename; the
    secrets.key file must always be a complete 32-byte file (no
    partial writes).
    """
    key_path = tmp_path / "secrets.key"
    key = load_or_create_master_key(key_path)
    assert len(key_path.read_bytes()) == 32
    # And the file mode is 0o600 on POSIX.
    import stat
    if hasattr(stat, "S_IMODE"):
        # On Windows the mode may not be honored; skip the check.
        import sys
        if sys.platform != "win32":
            mode = stat.S_IMODE(key_path.stat().st_mode)
            assert mode == 0o600, f"key file mode is {oct(mode)}, expected 0o600"
