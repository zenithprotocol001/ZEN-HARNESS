# ADR-0011: Model Configuration Storage

## Status

Accepted (v1.3.1, 2026-09-03).

## Context

v1.3.0 added live LLM provider dispatch (ADR-0009) but exposed
only one knob per session: the model id. Real provider APIs also
take `temperature`, `max_tokens`, `top_p`, and a system prompt;
without per-session control, users cannot tune a session to a
specific task (e.g. lower temperature for code review, higher
for creative writing).

We need a place to store these settings, per session, that
survives browser restarts and is encrypted at rest alongside the
API keys (which already live in `SecretsService`).

## Decision

Store model configs in `SecretsService` under the key
`model_config_{session_id}`. The value is a JSON object encrypted
with the v0x02 envelope (ADR-0010):

```json
{
  "temperature": 0.7,
  "max_tokens": 4096,
  "top_p": 1.0,
  "system_prompt": "You are a helpful assistant."
}
```

A new `ModelConfig` frozen dataclass holds the in-memory shape
and enforces value ranges in `__post_init__`:

- `temperature ∈ [0.0, 2.0]`
- `max_tokens ∈ [1, 8192]`
- `top_p ∈ [0.0, 1.0]`
- `system_prompt: str` (no length cap; the providers impose their
  own context-length limits)

A new `ModelConfigStore` wraps `SecretsService` and provides
`get_config(session_id) -> ModelConfig` and `set_config(session_id, config) -> None`.
A missing config returns a fresh `ModelConfig()` (defaults).

C1 exposes two new routes:

- `GET /api/sessions/{id}/config` → 200 `{temperature, max_tokens, top_p, system_prompt}`
- `POST /api/sessions/{id}/config` → 204 (body is the same JSON)

These mirror the existing `PATCH /api/sessions/{id}` route but
are scoped to a single concern (config) so the route surface
stays readable.

C7 reads the config in `_dispatch_live` and passes `temperature`,
`max_tokens`, and `top_p` as keyword args to the provider client.
`system_prompt` is prepended to the messages list **only if**
no `role: "system"` message is already present. (Pre-existing
system messages in the conversation win; the config is a
default, not an override.)

## Consequences

- **Positive**: Reuses `SecretsService`; configs are encrypted
  with the same v0x02 envelope as API keys; no new crypto.
- **Positive**: Per-session granularity; one user's "code review"
  config and another's "creative writing" config coexist.
- **Positive**: Default config is always available (fresh
  `ModelConfig()` if missing); the routes never 404.
- **Positive**: `ModelConfig.__post_init__` rejects out-of-range
  values at the data class boundary; the routes just call the
  constructor and let pydantic-equivalent validation fire.
- **Negative**: Configs and API keys share the same JSONL log;
  `list_secrets` will return `model_config_*` names alongside
  `llm_provider_*` names. Mitigation: the settings UI filters
  by prefix.
- **Neutral**: No inheritance / template system. A user wanting
  the same config across sessions must copy it manually.

## Implementation

- `src/dhc/services/model_config.py`: `ModelConfig` + `ModelConfigStore`.
- `src/dhc/modules/c1_gui_web_core/service.py`: two new routes
  registered via `app.router.add_get` / `add_post`.
- `src/dhc/serve_c1.py`: construct `ModelConfigStore(secrets)`
  eagerly, place it on `app["model_config_store"]`.
- `src/dhc/modules/c7_llm_stream_adapter/service.py`:
  - `LLMStreamAdapter.__init__` accepts an optional
    `config_store` kwarg.
  - `_dispatch_live` looks up the config (default if missing) and
    passes `temperature`, `max_tokens`, `top_p`, and the
    (possibly augmented) `messages` to the provider client.
- `src/dhc/integrations/base.py`: `LLMProvider.chat_stream`
  signature gains `temperature`, `max_tokens`, `top_p` kwargs
  (all optional, defaults `None` which means "provider default").
- `src/dhc/integrations/openai_client.py` /
  `anthropic_client.py` / `openrouter_client.py`: forward the
  kwargs into the request body.
- 10 backend tests in
  `tests/services/test_model_config.py` (5) and
  `tests/chat/test_model_config_routes.py` (5).
- 3 C7 dispatch tests in
  `tests/chat/test_c7_dispatch.py` (extend existing file).
- 3 frontend tests for `ModelConfigMenu.tsx` in
  `apps/web/src/__tests__/ModelConfigMenu.test.tsx`.

## References

- `docs/adr/0007-api-key-management.md` (secrets naming).
- `docs/adr/0010-per-secret-nonce.md` (v0x02 envelope).
- `docs/adr/0009-provider-abstraction.md` (LLMProvider ABC).
- `src/dhc/services/model_config.py` (the implementation).
- `tests/services/test_model_config.py` (the contract tests).
