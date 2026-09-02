# ADR-0010: Per-Secret Nonce in Envelope v0x02

## Status

Accepted (v1.3.1, 2026-09-03).

## Context

The v1.2.0 secrets envelope (`src/dhc/cordis/secrets.py`) uses a fixed
scrypt salt `b"dhc-secrets-v1"` to derive the keystream and MAC subkeys
from the master key. The nonce is already per-secret (16 random bytes
that get XORed into the keystream and the MAC), but the **scrypt KDF
salt is global**, which means every envelope uses the same KDF inputs
to derive the subkeys. Two envelopes that encrypt identical plaintexts
under the same master key therefore produce identical ciphertexts
(modulo the per-envelope nonce, which is bound but visible in the
envelope), which leaks "this is the same secret as that one" to anyone
who can read the log.

The v1.2.0 secrets log is encrypted at rest and the master key is in a
separate 0o600 file, so the threat is purely defense-in-depth: if an
attacker ever exfiltrates the log but not the key, they should not
learn the equality structure of the secrets.

## Decision

Bump the envelope version by changing the 4-byte ASCII header from
`DHC1` to `DHC2` and switching the scrypt salt to the per-envelope
nonce. The on-disk envelope layout is unchanged:

```
DHC2 || nonce (16) || ciphertext (n) || tag (32)
```

KDF:

```python
enc_key = scrypt(master_key + b"-ks", salt=nonce, n=2**10, r=8, p=1, dklen=32)
mac_key = scrypt(master_key + b"-mac", salt=nonce, n=2**10, r=8, p=1, dklen=32)
```

The MAC is over `(DHC2 || nonce || ct)` to stay consistent with the
existing format and to bind the version byte to the tag.

### Backward compatibility

- `open_envelope` dispatches on the 4-byte header:
  - `DHC1`: legacy salt `b"dhc-secrets-v1"`, identical to v1.2.0.
  - `DHC2`: per-envelope nonce as scrypt salt.
- New writes always produce `DHC2`.
- Old envelopes are read forever. There is **no auto-migration**;
  users can re-store secrets if they want them to inherit the new
  envelope (the re-store will succeed because the new write overlays
  the old one in the append-only log).

### Why not the spec'd `[version:1][salt:16][nonce:16]...` layout?

The v1.2.0 envelope is 4 header bytes + 16 nonce bytes + ct + 32 tag.
The spec in the v1.3.1 plan introduces a 1-byte version + 16-byte salt
+ 16-byte nonce, which is **17 bytes longer** and has a different
position for the nonce (offset 17 vs offset 4). Adopting it as-is would
break every existing v1.3.0 secret and require a migration script.

The header-byte approach preserves the envelope shape exactly, keeps
the on-disk size constant, and gives us the "per-secret KDF" property
the spec was after. It is a strict superset of v1.2.0.

## Consequences

- **Positive**: Every envelope is now encrypted with a unique KDF
  salt; identical plaintexts no longer leak equality.
- **Positive**: Backward compatible; v1.2.0 and v1.3.0 secrets are
  readable indefinitely.
- **Positive**: No size change; no migration script.
- **Negative**: Each secret now pays one scrypt call's cost (already
  ~50 ms; the 100 ms total is still dominated by the existing cost).
- **Neutral**: Old `DHC1` envelopes remain on disk until re-stored;
  the log is not compacted.

## Implementation

- `src/dhc/cordis/secrets.py`: `seal()` writes `DHC2`; `open_envelope()`
  dispatches on the 4-byte header. A new constant `HEADER_V2 = b"DHC2"`
  is added; `HEADER` is renamed to `HEADER_V1` for clarity, and the
  public alias `HEADER` continues to point at `HEADER_V1` to keep the
  `__all__` surface stable.
- 8 new tests in `tests/chat/test_secrets_v2.py`:
  1. `test_seal_v2_uses_per_envelope_salt` — two seals of the same
     plaintext produce different ciphertexts (because the scrypt salt
     differs, not just the nonce).
  2. `test_open_v2_round_trip` — seal then open returns the original.
  3. `test_open_v2_detects_ciphertext_tampering` — flip a bit in `ct`
     → `SecretEnvelopeError`.
  4. `test_open_v2_detects_nonce_tampering` — flip a bit in the nonce
     → `SecretEnvelopeError`.
  5. `test_open_v1_still_works` — a v1.2.0 envelope is decodable.
  6. `test_open_unknown_header_raises` — header `DHCX` → error.
  7. `test_seal_v2_envelope_layout` — bytes 0..3 are `DHC2`, bytes
     4..19 are the nonce (16 random bytes), bytes 20..n-32 are ct,
     last 32 are the tag.
  8. `test_secrets_service_round_trip_v2` — end-to-end via the
     `SecretsService` API on disk.
- `docs/secrets-model.md` updated with the v0x02 section.
- `GLOSSARY.md` adds "Per-Secret Nonce" entry.
- `scripts/invariants_check.ps1` grows one new positive check: a
  v1.2.0 smoke envelope can be read by the current code.

## References

- `src/dhc/cordis/secrets.py` (the implementation).
- `tests/chat/test_secrets_v2.py` (the contract tests).
- `docs/secrets-model.md` (the user-facing spec).
