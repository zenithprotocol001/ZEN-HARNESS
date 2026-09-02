# DHC Security Model

DHC is designed for the **single-user dev box, multi-process trust
boundary** threat model. It is **not safe for shared hosts** and never
will be. This document records what we defend against, what we
explicitly do not, and the structural rules that make the defenses
auditable.

## Threat model

In scope:

- Malicious or buggy **LLM output** that, when run as a Cordis plugin,
  attempts RCE, XSS, sandbox escape, auth bypass, supply-chain
  tampering, replay, or path traversal.
- **Cross-tab XSS** in the web client: an event payload or prompt body
  must never be able to execute script in another tab's context.
- **Supply-chain tampering** of a bundled plugin: an attacker who
  edits `service.py` after the manifest SHA is locked must not be able
  to load that plugin without the manifest also being updated.

Out of scope:

- A user with shell access on the dev box. They can read the bearer
  token file (`serve_c1.token`) and connect to the loopback port.
  This is by design.
- A second user on the same machine. The port is loopback; the token
  is shared.
- Network-level attacks. C1 binds to `127.0.0.1` only.

## Defense in depth

| Layer | Defense | Module / file |
|---|---|---|
| Browser CSP | `default-src 'self'`, no `unsafe-inline`, no `unsafe-eval`, `object-src 'none'`, `frame-ancestors 'none'` | C1 |
| HTML rendering | `dangerouslySetInnerHTML` only after `DOMPurify.sanitize` with `FORBID_TAGS` | `apps/web/src/sanitize.ts` |
| HTML localization | `renderMarkdown` / `renderToolResult` / `dangerouslySetInnerHTML` appear only in `panels/EventsPanel.tsx` | invariants + `App.tsx` negative invariant |
| Auth | Per-launch 256-bit bearer token, constant-time compare | C1 |
| Origin guard | WS handshake must be from a loopback origin | C1 |
| Plugin integrity | SHA-256 of `service.py` checked against `manifest.json` with `hmac.compare_digest` | `loader.py` |
| Manifest strictness | pydantic v2 with `extra="forbid"`, `strict=True` | `_manifest.py` |
| Capability policy | Deny-all default; no event listener can mutate the policy | C9 |
| Tool guard | Strict pydantic schemas; `BashInput.command` is `list[str]`, never a string | C4 |
| HMAC | `hmac.compare_digest` only; AST check forbids `==` on hmac-shaped names | C5, C8 |
| Replay protection | Nonce store with LRU eviction | C8 |
| PII scrubbing | `sk-*`, `sk_live_*`, `ghp_*`, email regex; recursive over dict/list/tuple/str | C10 |

## Per-module defenses

| Module | Attack | Defense | Test |
|---|---|---|---|
| C1 | XSS, CSP bypass, WS injection | CSP + DOMPurify + negative invariant | `tests/security/test_c1_xss.py` |
| C2 | History mutation, replay, prototype pollution | `tuple` immutability + frozen pydantic | `tests/security/test_c2_mutation.py` |
| C3 | Prompt injection, boundary breakout, tool schema smuggling | 9 boundary tokens escaped + capability-filtered tools | `tests/security/test_c3_boundary_injection.py` |
| C4 | Path traversal, command injection, schema bypass | Strict schemas; `BashInput.command` is `list[str]` | `tests/security/test_c4_path_traversal.py` |
| C5 | Signature forgery, capability tampering, separator smuggle | HMAC over `0x1F`-separated canonical form; `compare_digest` only | `tests/security/test_c5_spoofed_registration.py` |
| C6 | DoS via infinite loop | Hard step limit, emits `turn/end{reason: max_steps_exceeded}` | `tests/security/test_c6_infinite_loop_circuit_breaker.py` |
| C7 | Buffer overflow, malformed JSON, key leakage, partial chunks | 1 MiB buffer cap; key redaction; tolerant parser | `tests/security/test_c7_stream.py` |
| C8 | Replay, old timestamp, timing side-channel | LRU nonce store; ±5 min window; `compare_digest` only | `tests/security/test_c8_timing.py` |
| C9 | Capability escalation, listener smuggling | Deny-all default; only explicit `grant()` mutates | `tests/security/test_c9_escalation.py` |
| C10 | PII / secret leakage | Recursive scrubber | `tests/unit/test_c10.py` |
| Scorer | Additive formula regression, severity miscalibration | Multiplicative formula, security<50 floor | `tests/scoring/test_scorer.py` |
| Loader | Plugin supply-chain tampering | SHA-256 manifest pin + constant-time compare | `tests/plugins/test_manifest_and_loader.py` |

## What an auditor verifies

In order, on a stripped host without Python:

1. `scripts/static_check.ps1` — every `from dhc.X import Y` resolves.
2. `scripts/c8_static_check.ps1` — `hmac.compare_digest` is the only
   comparison; no `==` on hmac-shaped names.
3. `scripts/invariants_check.ps1` — 92 invariants across all 10
   modules, cordis, scorer, plugin marketplace, and the web client.
4. `scripts/hmac_sanity_check.ps1` — cross-language HMAC check.

On a provisioned host:

5. `pytest tests/ -q` — 242 tests pass.
6. `python -c "from dhc.scoring.scorer import make_report, write_report, ModuleScore; ..."` — DHC-V = 100.0.
7. Live `python -m dhc.serve_c1` + smoke `/healthz`, `/plugins`,
   `/prompts`, `/api/eval`. Verify CSP, origin, token.

## Known limitations

- A model that is **also** able to read the file system can read
  `serve_c1.token`. The marketplace is not a sandbox; it is a
  **contract** between the model and the harness.
- The web client trusts the Python bridge to embed the correct
  `<meta name="dhc-token">` value. A compromised bridge can deliver
  any token it wants.
- The eval pipeline runs the pasted code in a **subprocess**, not a
  sandbox. The subprocess inherits the harness's environment. This is
  acceptable for the offline eval use case (you are running code you
  already trust on your own machine), but the **paste-and-score
  feature in the browser is for trusted users only**.
