"""dhc.cordis.secrets: Authenticated at-rest envelope for the C2 secret log.

The harness runs on loopback (127.0.0.1) and the threat model for the
C2 secret log is "a casual file-system reader who can see the log
file but not the key file." We want the value to be unreadable
without the per-user key file at `~/.dhc/secrets.key`.

Why a hand-rolled construction instead of the `cryptography` package?

The runtime is a sandboxed Python 3.14 that does not have network
access and is missing the optional `cryptography` dependency. The
available stdlib primitives are `hashlib` (HMAC, SHA-2, SHA-3, SHAKE)
and `hmac`. We build an authenticated stream cipher on top of those.

Envelope versions (ADR-0010):

  v0x01  (HEADER="DHC1") — fixed scrypt salt b"dhc-secrets-v1".
  v0x02  (HEADER="DHC2") — per-envelope nonce as the scrypt salt.

Both versions use the same on-disk layout:

  HEADER (4) || nonce (16) || ciphertext (n) || tag (32)

`open_envelope` dispatches on the header. New writes always produce
`DHC2`; old envelopes remain readable indefinitely.

Construction (encrypt-then-MAC, NIST SP 800-108 counter mode):

    K    = scrypt-derived 32-byte key from the key file
    For each write:
        nonce = urandom(16)
        ks_i  = HMAC-SHA256(K_ks, nonce || ctr_be32(i))   for i = 0,1,...
        ct    = plaintext XOR (ks_0 || ks_1 || ...)
        tag   = HMAC-SHA256(K_mac, header || nonce || ct)   separate MAC key

Keystream construction is HMAC-SHA256 in counter mode (sometimes
called "HMAC-CTR"). The HMAC is keyed with a domain-separated
subkey `K_ks`; the counter `i` is a big-endian 32-bit integer
incremented per 32 bytes of output. This is a standard
construction — the underlying primitive is HMAC-SHA256, a PRF, so
the keystream is indistinguishable from random to any adversary
without the key. The authentication tag is computed with a separate
domain-separated subkey `K_mac` over `(header || nonce || ct)`.

Threat model:

The primary defense is the loopback bind. The encryption is defense
in depth: an attacker who can read `~/.dhc/secrets.log` but not
`~/.dhc/secrets.key` (mode 0o600) cannot decrypt values or forge
a tag. An attacker who has both files has full access; the
encryption is not designed to resist that.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets as _secrets
import struct
import threading
from pathlib import Path


# ---------- Keystream: HMAC-SHA256 in counter mode ----------

_KEYSTREAM_BLOCK = 32  # HMAC-SHA256 output size

# v0x01 KDF salt (fixed, kept for backward-compatible reads).
_SALT_V1 = b"dhc-secrets-v1"


def _derive_keys(master_key: bytes, salt: bytes) -> tuple[bytes, bytes]:
    """Derive (keystream_key, mac_key) from the master key.

    `salt` is the scrypt KDF salt. v0x01 envelopes pass the fixed
    `b"dhc-secrets-v1"` salt; v0x02 envelopes pass the per-envelope
    nonce so each secret has a unique KDF input.

    The parameters (`n=2**10, r=8, p=1`) take ~50 ms on a modern CPU;
    secrets.put/get are not on the hot path.
    """
    if len(master_key) != 32:
        raise ValueError("master key must be 32 bytes")
    enc = hashlib.scrypt(master_key + b"-ks", salt=salt, n=2**10, r=8, p=1, dklen=32)
    mac = hashlib.scrypt(master_key + b"-mac", salt=salt, n=2**10, r=8, p=1, dklen=32)
    return enc, mac


def _keystream(ks_key: bytes, nonce: bytes, length: int) -> bytes:
    """Generate `length` bytes of HMAC-SHA256-counter-mode keystream.

    `nonce` is mixed into every block; the counter `i` is a 32-bit
    big-endian integer incremented per 32 bytes of output.
    """
    if length < 0:
        raise ValueError("length must be non-negative")
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(
            ks_key,
            nonce + struct.pack(">I", counter),
            hashlib.sha256,
        ).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


# ---------- Envelope: encrypt-then-MAC ----------

NONCE_LEN = 16
TAG_LEN = 32

# v0x01 header (4 ASCII bytes). v0x02 is `DHC2`.
HEADER_V1 = b"DHC1"
HEADER_V2 = b"DHC2"

# Public alias: `HEADER` keeps the v1.2.x name; new code should use
# `HEADER_V1` / `HEADER_V2` directly.
HEADER = HEADER_V1


class SecretEnvelopeError(Exception):
    """Raised when an envelope cannot be decoded or fails the MAC check."""


def _seal_with(plaintext: bytes, master_key: bytes, header: bytes) -> bytes:
    """Internal: encrypt under a chosen header.

    The MAC is computed over `header || nonce || ct` so the header
    is bound to the tag.
    """
    if not isinstance(plaintext, (bytes, bytearray)):
        raise TypeError("plaintext must be bytes")
    if not isinstance(master_key, (bytes, bytearray)) or len(master_key) != 32:
        raise ValueError("master_key must be 32 bytes")
    nonce = _secrets.token_bytes(NONCE_LEN)
    if header == HEADER_V2:
        salt = nonce
    elif header == HEADER_V1:
        salt = _SALT_V1
    else:
        raise ValueError(f"unsupported envelope header: {header!r}")
    ks_key, mac_key = _derive_keys(bytes(master_key), salt)
    ks = _keystream(ks_key, nonce, len(plaintext))
    ct = bytes(a ^ b for a, b in zip(plaintext, ks))
    tag = hmac.new(mac_key, header + nonce + ct, hashlib.sha256).digest()
    return header + nonce + ct + tag


def seal(plaintext: bytes, master_key: bytes) -> bytes:
    """Encrypt-then-MAC a plaintext under the master key.

    Returns: HEADER (4) || nonce (16) || ciphertext (n) || tag (32)

    Since v1.3.1 the header is `DHC2` (per-envelope nonce as the
    scrypt KDF salt). v1.2.0 / v1.3.0 `DHC1` envelopes are still
    readable via `open_envelope`.
    """
    return _seal_with(plaintext, master_key, HEADER_V2)


def open_envelope(envelope: bytes, master_key: bytes) -> bytes:
    """Open an envelope produced by `seal`. Verifies the tag in
    constant time and returns the plaintext. Raises
    `SecretEnvelopeError` on any failure.

    Dispatches on the 4-byte header:

    - `DHC1` (v0x01): legacy fixed-salt envelopes from v1.2.0/v1.3.0.
    - `DHC2` (v0x02): v1.3.1+ envelopes with a per-envelope nonce
      as the scrypt salt.

    Unknown headers raise `SecretEnvelopeError` (we do NOT fall
    through; this is the v0x03-onwards rejection path).
    """
    if not isinstance(envelope, (bytes, bytearray)):
        raise TypeError("envelope must be bytes")
    if not isinstance(master_key, (bytes, bytearray)) or len(master_key) != 32:
        raise ValueError("master_key must be 32 bytes")
    if len(envelope) < len(HEADER_V1) + NONCE_LEN + TAG_LEN:
        raise SecretEnvelopeError("envelope too short")
    header = bytes(envelope[: len(HEADER_V1)])
    # Header is always 4 bytes; both v0x01 and v0x02 use the same
    # 4 + 16 + n + 32 layout, so the nonce/ct/tag offsets are stable.
    nonce = bytes(envelope[len(HEADER_V1) : len(HEADER_V1) + NONCE_LEN])
    tag = envelope[-TAG_LEN:]
    ct = envelope[len(HEADER_V1) + NONCE_LEN : -TAG_LEN]
    if header == HEADER_V1:
        salt = _SALT_V1
    elif header == HEADER_V2:
        # v0x02: the KDF salt is the per-envelope nonce.
        salt = nonce
    else:
        raise SecretEnvelopeError(f"unknown envelope header: {header!r}")
    ks_key, mac_key = _derive_keys(bytes(master_key), salt)
    expected_tag = hmac.new(mac_key, header + nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(expected_tag, tag):
        raise SecretEnvelopeError("authentication failed")
    ks = _keystream(ks_key, nonce, len(ct))
    return bytes(a ^ b for a, b in zip(ct, ks))


# ---------- Key file management ----------


DEFAULT_KEY_PATH = Path.home() / ".dhc" / "secrets.key"


def _ensure_dir(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(os, "chmod"):
        try:
            os.chmod(p.parent, 0o700)
        except OSError:
            pass


def load_or_create_master_key(key_path: Path = DEFAULT_KEY_PATH) -> bytes:
    """Load the 32-byte master key from `key_path`, creating a fresh
    one on first call. The key file is created with mode 0o600 on
    POSIX systems; on Windows the default ACL is sufficient given the
    loopback-bind threat model.
    """
    _ensure_dir(key_path)
    if key_path.exists():
        data = key_path.read_bytes()
        if len(data) != 32:
            raise SecretEnvelopeError(f"key file has wrong length: {len(data)}")
        return data
    key = _secrets.token_bytes(32)
    # Write atomically: tmp file then rename.
    tmp = key_path.with_suffix(".tmp")
    tmp.write_bytes(key)
    if hasattr(os, "chmod"):
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
    os.replace(tmp, key_path)
    if hasattr(os, "chmod"):
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
    return key


# ---------- Public SecretsService ----------


class SecretsService:
    """In-process secret store backed by an append-only JSONL log
    on disk. Each entry is an envelope (bytes) written base64 to the
    log. Reads decrypt the latest envelope for a given name.

    The log file is `secrets_dir / "secrets.log"`. Reads scan from
    the start and apply operations in order, so a `del`
    is honored even though the log is append-only.
    """

    def __init__(self, secrets_dir: Path) -> None:
        self._dir = Path(secrets_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._log = self._dir / "secrets.log"
        self._key = load_or_create_master_key(self._dir / "secrets.key")
        self._lock = threading.Lock()
        # In-memory cache: name -> plaintext (or None if deleted).
        # Populated lazily on first read so writes during a session
        # are reflected without a full rescan.
        self._cache: dict[str, bytes | None] = {}

    # ----- public API -----

    def put(self, name: str, value: str) -> None:
        """Encrypt and persist a secret. `value` is treated as utf-8."""
        if not isinstance(name, str) or not name:
            raise ValueError("name must be a non-empty string")
        if not isinstance(value, str):
            raise ValueError("value must be a string")
        self.put_raw(name, value.encode("utf-8"))

    def put_raw(self, name: str, value: bytes) -> None:
        """Encrypt and persist a raw `bytes` value. Used by stores
        that need to round-trip binary data (e.g. JSON-encoded
        `ModelConfig` blobs in ADR-0011)."""
        if not isinstance(name, str) or not name:
            raise ValueError("name must be a non-empty string")
        if not isinstance(value, (bytes, bytearray)):
            raise TypeError("value must be bytes")
        envelope = seal(bytes(value), self._key)
        record = {"op": "set", "name": name, "blob": base64.b64encode(envelope).decode("ascii")}
        with self._lock:
            with self._log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, separators=(",", ":")) + "\n")
            self._cache[name] = bytes(value)

    def get(self, name: str) -> str | None:
        raw = self.get_raw(name)
        return raw.decode("utf-8") if raw is not None else None

    def get_raw(self, name: str) -> bytes | None:
        """Decrypt and return the raw `bytes` value for `name`,
        or `None` if the name is missing or tombstoned.

        Used by stores that need binary round-trip (ADR-0011
        `ModelConfigStore`).
        """
        with self._lock:
            if name in self._cache:
                cached = self._cache[name]
                return cached
            self._replay()
            cached = self._cache.get(name)
            return cached

    def delete(self, name: str) -> bool:
        with self._lock:
            self._replay()
            # Only emit a tombstone if the name currently resolves to
            # a live (non-deleted) value. Deleting a missing key (or
            # one that is already tombstoned) is a no-op and returns
            # False, so the log stays compact and idempotent.
            current = self._cache.get(name)
            if current is None:
                return False
            record = {"op": "del", "name": name}
            with self._log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, separators=(",", ":")) + "\n")
            self._cache[name] = None
            return True

    def list(self) -> list[str]:
        with self._lock:
            self._replay()
            return sorted(n for n, v in self._cache.items() if v is not None)

    # ----- internals -----

    def _replay(self) -> None:
        self._cache = {}
        if not self._log.exists():
            return
        for line in self._log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            op = rec.get("op")
            name = rec.get("name")
            if op == "set":
                blob = base64.b64decode(rec["blob"])
                pt = open_envelope(blob, self._key)
                self._cache[name] = pt
            elif op == "del":
                self._cache[name] = None
            else:
                # Unknown op: ignore but do not fail; the log is
                # forward-compatible.
                pass


__all__ = [
    "DEFAULT_KEY_PATH",
    "NONCE_LEN",
    "TAG_LEN",
    "HEADER",
    "HEADER_V1",
    "HEADER_V2",
    "SecretEnvelopeError",
    "SecretsService",
    "load_or_create_master_key",
    "open_envelope",
    "seal",
]
