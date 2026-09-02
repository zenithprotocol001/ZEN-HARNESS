# GLOSSARY

| Term | Definition |
|---|---|
| **Cordis** | The TypeScript framework for spatiotemporal composability that the DHC harness ports to Python. |
| **Context** | The Cordis service registry + event bus + disposable stack. One per harness instance. |
| **EventEmitter** | The Cordis event bus. Supports `on`, `off`, `emit`, and `waterfall`. |
| **waterfall** | An event where each listener receives the previous listener's return value and may mutate it. |
| **dispose** | The cleanup function a plugin returns from `apply`. Registered on the context's disposable stack; run in reverse on `ctx.dispose()`. |
| **`@plugin`** | The decorator that turns a plain `apply(ctx, config)` function into a Cordis plugin. |
| **manifest** | A pydantic-validated JSON file describing a plugin: `id`, `name`, `version`, `entrypoint`, `events`, `config_schema`, `sha256`. |
| **DHC-V** | The DeepSeek Harness Creation Value score: `functionality * (security / 100)`, with a hard floor at `security < 50`. |
| **functionality_score** | `unit_pass_rate * 40 + turn_completion_rate * 40 + ui_streaming_fidelity * 20`. Re-weighted to 50/50 if Playwright is unavailable. |
| **security_score** | Starts at 100; deducts per `Finding`. `critical` floors the score. |
| **floor_triggered** | `True` iff a `critical` finding is present or the post-deduction security score is `< 50`. |
| **`production_ready`** | DHC-V ≥ 80. |
| **`experimental`** | DHC-V 50 – 79. |
| **`unsafe`** | DHC-V < 50. |
| **C1..C10** | The 10 core modules. See `docs/architecture.md`. |
| **tool guard** | C4. Schema-strict tool invocation with security checks. |
| **capability policy** | C9. Deny-all default; intercepts `tools/pre-execute`. |
| **`pre-execute`** | The Cordis event fired before any tool call; C9 listens to it. |
| **boundary tokens** | C3. The 9 special tokens escaped before the user section is wrapped: `<|user_start|>`, `<|user_end|>`, etc. |
| **HMAC-SHA256** | The keyed hash used in C5 (agent manifest) and C8 (webhook). |
| **`compare_digest`** | The constant-time comparison from `hmac.compare_digest`. The **only** legal way to compare HMACs. |
| **nonce** | A unique value sent with a webhook to defeat replay. C8 stores them in a bounded LRU. |
| **timestamp window** | ±5 minutes. Webhooks outside this window are rejected as `ExpiredTimestamp`. |
| **replay** | Re-submission of a previously seen webhook. Defeated by the nonce store. |
| **loopback** | `127.0.0.1`. C1 only binds to loopback. |
| **bearer token** | A 256-bit secret in `serve_c1.token`; required on every WS handshake and HTTP request to C1. |
| **CSP** | Content-Security-Policy. The HTTP header that tells the browser what is allowed to load. |
| **DOMPurify** | The XSS sanitizer used in the web client. |
| **FORBID_TAGS** | The list of tags DOMPurify will strip: `script`, `style`, `iframe`, `object`, `embed`, `form`, `input`. |
| **`dangerouslySetInnerHTML`** | React's escape hatch for raw HTML. Only used in `panels/EventsPanel.tsx`, always after DOMPurify. |
| **ephemeral port** | A port picked by the OS at runtime. C1 picks one, writes it to `serve_c1.port`. |
| **XSS** | Cross-Site Scripting. The attack class that CSP + DOMPurify + the negative invariant defend against. |
| **RCE** | Remote Code Execution. The attack class that the manifest SHA pin + the `BashInput.command` schema defend against. |
| **supply chain** | The path from plugin author to load time. Defended by the SHA-256 pin. |
| **SHA-256 pin** | The 64-hex digest of `service.py` stored in `manifest.json` and verified at load time. |
| **manifest integrity** | The property that the on-disk `service.py` matches the SHA in `manifest.json`. |
| **paste-and-score** | The browser feature that lets you paste LLM output and run it through the eval pipeline. |
| **offline eval** | The `run_llm_eval.py` wrapper that runs the full 10-prompt eval without network access. |
| **master prompt** | One of the 10 prompts in `src/dhc/eval/prompts/`. Each is the rubric for one C-module. |
| **waterfall event** | An event whose value flows through listeners and may be mutated. `agent/pre-step` is the canonical example. |
| **`turn/start`** | The first event in a turn. |
| **`step/start`** | Emitted at the beginning of each step. |
| **`llm/stream`** | Emitted for every chunk of the LLM response. |
| **`tool/call`** | Emitted when the LLM decides to invoke a tool. |
| **`step/end`** | Emitted at the end of each step. |
| **`turn/end`** | The last event of a turn. `reason` ∈ `ABORT_REASONS`. |
| **`ABORT_REASONS`** | `completed`, `max_steps_exceeded`, `tool_error`, `policy_denied`, `llm_error`. |
| **mock LLM** | The deterministic aiohttp server in `fixtures/mock_llm/` that pretends to be an OpenAI-compatible provider. |
| **frozen epoch** | `FROZEN_EPOCH_MS` in `fixtures/mock_llm/scripts.py`. All timestamps in tests are pinned to `2026-01-01T00:00:00Z`. |
| **evaluator** | The thing being scored. Usually an LLM producing a plugin module. |
| **`Finding`** | The dataclass `dhc.scoring.scorer.Finding(module, severity, description)`. |
| **`ModuleScore`** | The dataclass `dhc.scoring.scorer.ModuleScore(module, functionality, security, findings, notes)`. |
| **relay** | The `relay/` folder where the versioned zip artifacts live. |
| **invariants** | The PowerShell-based static checks in `scripts/invariants_check.ps1`. 92 of them at v1.1.0. |
| **static check** | The set of PowerShell scripts that verify the harness without running Python. |
| **envelope** | The encrypted at-rest blob in `~/.dhc/secrets/secrets.log`. Format: 4-byte header (`DHC1` or `DHC2`) + 16-byte nonce + ciphertext + 32-byte tag. |
| **per-secret nonce** | The 16 random bytes stored in the envelope header slot. Used as the scrypt KDF salt in `DHC2` envelopes (v1.3.1+) so every secret gets a unique KDF. |
| **model config** | Per-session LLM parameters (temperature, max_tokens, top_p, system_prompt) stored encrypted in `SecretsService` under the key `model_config_{session_id}`. |
| **token usage** | The `prompt_tokens` + `completion_tokens` counts returned by an LLM provider. Surfaced in v1.3.1 via the `usage` field on the final `StreamChunk`. |
| **`DHC1`** | The v0x01 envelope header. Fixed scrypt salt; read-only since v1.3.1. |
| **`DHC2`** | The v0x02 envelope header. Per-envelope nonce as scrypt salt; default since v1.3.1. |
