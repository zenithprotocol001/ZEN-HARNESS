# ADR-0007 — API Key Management

- **Status**: Accepted (2026-09-02)
- **Deciders**: harness maintainers
- **Date**: 2026-09-02
- **Supersedes**: (none)
- **Related**: ADR-0006 (Model Selection Strategy), `docs/secrets-model.md`

## Context and problem statement

v1.3.0 introduces live LLM providers (OpenAI, Anthropic, OpenRouter). Each call to a live provider requires a per-user API key. The v1.2.0 release already ships `SecretsService` (an HMAC-SHA256 encrypt-then-MAC envelope at `~/.dhc/secrets/secrets.log`) and the C1 routes `GET /api/secrets`, `PUT /api/secrets/{name}`, `DELETE /api/secrets/{name}`. The v1.2.0 Salt strategy section also reserves headroom for a per-secret random nonce in the envelope format.

We need to decide:

1. How are API keys for live providers stored and retrieved?
2. How is the (provider, model) → secret name mapping done?
3. What happens when a key is missing?
4. Does v1.3.0 also implement the per-secret nonce promised in v1.2.0?

## Decision drivers

- The existing `SecretsService` is already loopback-only, encrypted at rest, and has a stable HTTP surface. Reusing it is much cheaper than introducing `keyring` or another backend.
- v1.2.0 already locks the `Session.model` field; per-session model selection is therefore a no-schema-change feature.
- The chat UX must surface a missing key in a way that does not crash the WS handler.

## Considered options

### Option A — Store in OS keychain (`keyring`)

Pros: industry-standard, OS-managed.
Cons: heavy dependency, different backends per OS (Credential Manager / Secret Service / Keychain), no v1.2.x test surface to reuse. Blocked by the sandbox (no `keyring` available in the runtime).

### Option B — Store as plaintext in `~/.dhc/secrets/api_keys.json`

Pros: trivial.
Cons: a local file reader sees the keys. Rejected — the v1.2.0 threat model explicitly excludes this.

### Option C (chosen) — Reuse `SecretsService` with a per-provider naming convention

The secret name is:

```
llm_provider_{provider}_{model_id}
```

Where `{model_id}` is the part of the canonical id after the provider prefix. Examples:

- `llm_provider_openai_gpt-4o-mini`
- `llm_provider_anthropic_claude-3-5-sonnet-latest`
- `llm_provider_openrouter_auto`
- `llm_provider_mock-llm_default` (empty value; the mock doesn't need a key but the name exists so the UI shows a single list)

The factory `provider_client_for(model)` looks up the key with `secrets_service.get(name)`. If the key is missing AND the model is not the mock, `ProviderError(status=401, message="missing api key for {model_id}")` is raised. The C1 chat WS handler closes with code `1011` and a payload of `{"error": "missing api key for {model_id}"}`. The React `ChatPanel` shows a "Missing API key" banner with a `curl` hint (Settings UI is deferred to v1.3.1).

## Decision

**Option C.** The `SecretsService` is the single source of truth for API keys. The naming convention is locked by this ADR. The per-secret random nonce promised in v1.2.0's Salt strategy is **deferred to v1.3.1** to keep v1.3.0 scoped to the live-provider rollout.

## Consequences

- **Positive**: zero new dependencies. The existing 23 `test_secrets.py` tests cover the storage layer; the v1.3.0 provider client tests can use the existing `tmp_path` pattern.
- **Positive**: keys are encrypted at rest with the v1.2.0 envelope. The 23 tamper-detection tests in v1.2.0 already cover the surface.
- **Negative**: the v1.3.0 user has to `curl` keys in (or wait for v1.3.1's Settings modal). The README's "Quick start" section gets a one-liner with the exact `curl` invocation.
- **Negative**: a single user can have at most one key per (provider, model) pair. Multi-key rotation is out of scope.
- **Risk**: a user puts their OpenAI key into the wrong field (e.g. `name="openai-key"` instead of `name="llm_provider_openai_gpt-4o-mini"`). Mitigation: the v1.3.0 Settings UI (deferred) will have a dropdown of known `(provider, model)` pairs, so the field is pre-filled. Until then, the README documents the naming convention prominently.

## Compliance

- The `package_relay.ps1` exclude list must drop `~/.dhc/secrets/` if it ever appears in the repo (verify in v1.3.0 cleanup). Currently the path is not in the repo so no `.gitignore` change is required.
- The 23 secrets tests from v1.2.0 are the contract surface; no new secrets tests are added in v1.3.0.
- The chat WS handler test (`test_c7_dispatch.py::test_c7_dispatch_missing_api_key_raises`) asserts the missing-key behavior.
- A new invariant in `scripts/invariants_check.ps1` asserts that every model in `ModelRegistry` has a corresponding `(provider, model_id)` pair covered by the naming convention (i.e. `model.id.partition("/")[2]` is non-empty).
