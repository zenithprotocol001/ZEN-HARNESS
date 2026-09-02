# ADR-0002 — Plugin marketplace with SHA-256 manifest integrity

- **Status:** Accepted
- **Date:** 2026-09-01
- **Authors:** DHC maintainers

## Context

The harness is meant to evaluate LLM-generated Cordis plugins. Plugins
are Python code that runs in the same process as the harness. This is
the dominant supply-chain risk in the system: a model that emits a
plugin that reads `serve_c1.token` and exfiltrates it has
**bypassed every other defense in the harness**.

Three approaches were considered:

1. **Sandbox** — run plugins in a separate process with seccomp or
   a container. Rejected as disproportionate; the harness is a single-
   user dev tool, and the operating cost of maintaining a sandbox
   under Windows + macOS + Linux is too high for the threat model.
2. **No third-party plugins** — only the 10 core modules. Rejected;
   the whole point of the marketplace is to let models **create**
   plugins, and a marketplace with no third-party content is not a
   marketplace.
3. **Manifest + SHA-256 pin + constant-time compare** — chosen.

## Decision

Every plugin ships `manifest.json` alongside `service.py`:

```json
{
  "id": "rate_limiter_v1",
  "name": "Rate Limiter",
  "version": "0.1.0",
  "description": "...",
  "entrypoint": "service:rate_limiter_v1",
  "events": ["tool/call"],
  "config_schema": { ... },
  "sha256": "<64-hex>"
}
```

The manifest is validated with pydantic v2 (`extra="forbid"`,
`strict=True`). Unknown fields are rejected.

At load time, the loader:

1. Recomputes the SHA-256 of `service.py`.
2. Compares it to the manifest's `sha256` with
   `hmac.compare_digest`. **Constant-time only.**
3. On mismatch, raises `PluginIntegrityError` and refuses to import
   the module.

The 5 bundled plugins' SHAs are **locked** in
[`../SHA-PINNING.md`](../SHA-PINNING.md) and asserted by
`scripts/invariants_check.ps1`.

## Consequences

- A model that modifies `service.py` without updating the manifest
  SHA fails to load. The error is loud and immediate.
- A model that modifies **both** files is no longer an integrity
  violation but is visible in code review: the SHA in
  `SHA-PINNING.md` is now out of date and the next maintainer
  notices.
- An auditor can verify integrity with one command:
  `python -m dhc.plugins._sha <id>`.
- The cost per plugin is one extra file (the manifest) and one
  `compare_digest` call per load.
- A determined attacker who can write to both `service.py` and
  `manifest.json` is **not** stopped. This is accepted; the threat
  model is a misbehaving LLM, not a malicious local user.
