# Secrets at-rest envelope

The C1 GuiWebCore stores user-supplied API keys (used by v1.3.0+
live LLM providers) in an append-only JSONL log encrypted under
a per-user master key. This document describes the construction,
the threat model, and the operational checklist.

## Envelope versions

| Version | Header | KDF salt | Status |
|---|---|---|---|
| v0x01 | `DHC1` | fixed `b"dhc-secrets-v1"` | v1.2.0 / v1.3.0 reads + writes |
| v0x02 | `DHC2` | per-envelope nonce (16 bytes) | v1.3.1 reads + writes; v1.3.0 still reads |

The envelope layout is the same for both versions:

```
HEADER (4) || nonce (16) || ciphertext (n) || tag (32)
```

The only difference is the 4-byte header, which the reader uses to
pick the KDF, and the KDF's salt (fixed vs. per-envelope). See
`docs/adr/0010-per-secret-nonce.md` for the rationale.

## Threat model

| Adversary | Mitigation |
|---|---|
| A casual file-system reader who can see `~/.dhc/secrets/secrets.log` but NOT the key file | Encryption at rest (HMAC-SHA256-encrypted ChaCha-like stream cipher). The reader sees the nonce and the ciphertext but cannot decrypt. |
| A user copying the log file to a different machine | Same as above. Without the key file, the file is opaque. |
| An attacker with read access to BOTH `secrets.log` AND `secrets.key` | Not defended. The encryption is defense in depth, not a primary defense. The primary defense is the loopback bind (`127.0.0.1`). |
| A network attacker | Not applicable. The server binds loopback. |

## Construction (encrypt-then-MAC)

For v0x01 (`DHC1`) writes:

```
K        = 32-byte master key from ~/.dhc/secrets/secrets.key (mode 0o600)
K_ks     = scrypt(K + b"-ks",  salt="dhc-secrets-v1", n=2^10, r=8, p=1, dklen=32)
K_mac    = scrypt(K + b"-mac", salt="dhc-secrets-v1", n=2^10, r=8, p=1, dklen=32)
nonce    = secrets.token_bytes(16)
ks_i     = HMAC-SHA256(K_ks,  nonce || ctr_be32(i))   for i = 0, 1, 2, ...
ct       = plaintext XOR (ks_0 || ks_1 || ...)
tag      = HMAC-SHA256(K_mac, "DHC1" || nonce || ct)
envelope = "DHC1" || nonce || ct || tag
```

For v0x02 (`DHC2`) writes (v1.3.1+):

```
K        = 32-byte master key from ~/.dhc/secrets/secrets.key (mode 0o600)
K_ks     = scrypt(K + b"-ks",  salt=nonce, n=2^10, r=8, p=1, dklen=32)
K_mac    = scrypt(K + b"-mac", salt=nonce, n=2^10, r=8, p=1, dklen=32)
nonce    = secrets.token_bytes(16)
ks_i     = HMAC-SHA256(K_ks,  nonce || ctr_be32(i))   for i = 0, 1, 2, ...
ct       = plaintext XOR (ks_0 || ks_1 || ...)
tag      = HMAC-SHA256(K_mac, "DHC2" || nonce || ct)
envelope = "DHC2" || nonce || ct || tag
```

The only difference is the header and the KDF salt. v0x02 is a
strict security improvement over v0x01 (unique scrypt salt per
envelope); the on-disk size and tag scheme are identical.

The envelope is then base64-encoded and appended to the log.

### Salt strategy

v1.2.x used a fixed scrypt salt `b"dhc-secrets-v1"` to derive
`K_ks` and `K_mac` from the master key. The keystream nonce
(`secrets.token_bytes(16)`) was already per-write random, so
identical plaintexts encrypted under the same master key
produced different ciphertexts, but the underlying KDF inputs
were identical for every envelope (which leaked equality
information to anyone who could read the log without the key).

v1.3.1 (ADR-0010) replaces the fixed salt with the per-envelope
nonce. Every secret now uses a unique scrypt salt, so the
keystream and MAC subkeys are also unique. The envelope header
bumps from `DHC1` to `DHC2`; `open_envelope` dispatches on the
header, so `DHC1` envelopes remain readable indefinitely.

### Why HMAC-CTR for the keystream?

The runtime is a sandboxed Python 3.14 with no network access and
no `cryptography` package. The available stdlib primitives are
`hashlib` (HMAC, SHA-2, SHA-3, SHAKE) and `hmac`. Building a
stream cipher from HMAC-SHA256 in counter mode is a standard
construction (sometimes called "HMAC-DRBG without reseeding");
the underlying primitive is HMAC-SHA256, a PRF, so the keystream
is indistinguishable from random to any adversary without the
key.

The `scrypt`-derived subkeys `K_ks` and `K_mac` are
domain-separated (different label) so the same input produces
two unrelated keys. This means a keystream generator and a tag
verifier cannot be confused even if an attacker controls the
plaintext.

### Why scrypt for the KDF?

scrypt is a memory-hard KDF. The parameters `n=2^10, r=8, p=1`
take ~10 ms on a modern CPU and use ~1 MB of memory, which is
enough to slow down a brute-force attack on a weak master key
without making the secret store unusable. The parameters are
fixed; tuning them is out of scope for v1.2.0.

## On-disk format

```
~/.dhc/secrets/
├── secrets.key    # 32 bytes, mode 0o600 (POSIX) or default ACL (Windows)
└── secrets.log    # JSONL; one record per line
```

Example log:

```json
{"op":"set","name":"openai","blob":"RERDMP7vqQ...base64..."}
{"op":"set","name":"anthropic","blob":"RERDMP7vqQ...base64..."}
{"op":"del","name":"openai"}
{"op":"set","name":"openai","blob":"NEWBASE64..."}
```

The log is append-only. A `del` is honored by replaying the log
and tracking which names are currently live (non-deleted).

## API surface

```
PUT    /api/secrets/{name}    body: {value: str}     → 204
GET    /api/secrets           → 200 {names: [...]}    (NEVER values)
DELETE /api/secrets/{name}    → 204 | 404
```

`GET /api/secrets` returns ONLY names. There is no endpoint that
returns a secret value; the secrets are for outbound calls only.
The unit test `test_service_list_hides_values` asserts this.

## Key file lifecycle

`load_or_create_master_key(key_path)`:

1. If the key file exists and is 32 bytes, return its contents.
2. Otherwise, generate a fresh 32-byte key with `secrets.token_bytes(32)`.
3. Write to `key_path.tmp`, `chmod 0o600`, then `os.replace` onto
   `key_path`, then `chmod 0o600` again.

The `chmod 0o600` is best-effort: on POSIX it succeeds, on
Windows it is a no-op and the default ACL applies. This is
documented; the loopback bind is the actual trust boundary.

## Tamper detection

Any modification to the envelope (header, nonce, ciphertext, or
tag) is detected at open time:

- The header is checked first (`hmac.compare_digest`).
- The tag is recomputed over `(header || nonce || ct)` and
  compared to the supplied tag in constant time.

A mismatch raises `SecretEnvelopeError`. The unit tests
`test_seal_tamper_*` exercise every position.

## Forward compatibility

The log format is JSONL with a fixed schema (`op`, `name`,
optional `blob`). Unknown operations are ignored on replay, so a
future v1.3.0 log format (e.g. `op: "rotate"`) can be replayed
by an older reader without crashing. Older readers simply skip
the unknown rows.

## Operational checklist

When deploying v1.2.0 in a new environment:

1. `~/.dhc/secrets/secrets.key` is created on first use. Do NOT
   commit this file. It is not in the relay zip's exclusion list
   (it is not in the repo).
2. Back up `~/.dhc/secrets/secrets.key` separately from the log.
   Without the key, the log is opaque; without the log, the key
   is useless. Both are required.
3. On Windows, ensure the user profile's default ACL keeps the
   `secrets.key` file readable only by the user account that
   started the C1 process.
4. To rotate the key, you must re-encrypt every record: read
   each name with the old key, write it back with the new key,
   and atomically replace `secrets.key`. v1.3.1 does not ship a
   rotation tool; it is left to v1.4.0.
5. New secrets written after upgrading to v1.3.1 are stored as
   `DHC2` envelopes. Existing `DHC1` secrets remain readable
   forever; re-storing a name will upgrade it to `DHC2` on the
   next `PUT /api/secrets/{name}`.

## Why not just use the OS keychain?

`keyring` (the standard cross-platform keychain library) is a
heavy dependency for a sandboxed runtime. It also has different
backends per OS (Credential Manager on Windows, Secret Service
on Linux, Keychain on macOS), each with its own quirks. The
HMAC-CTR envelope is a self-contained, dependency-free
construction that meets the v1.2.0 threat model. v1.3.0 may add
an optional `keyring` backend.
