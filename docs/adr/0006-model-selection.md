# ADR-0006 — Model Selection Strategy

- **Status**: Accepted (2026-09-02)
- **Deciders**: harness maintainers
- **Date**: 2026-09-02

## Context and problem statement

v1.3.0 will add live LLM providers (OpenAI, Anthropic, OpenRouter)
on top of the v1.2.0 mock LLM surface. v1.2.x currently exposes a
single model (`mock-llm/default`) and the `ChatPanel` has no model
selector. The `Session` dataclass already carries a `model` field
(populated automatically on session creation) so per-session model
selection is technically a no-schema-change feature.

We need a coherent story for how the model list is sourced and
how the UI surfaces it.

## Decision drivers

- v1.2.x must remain shippable with no outbound network
  reachability required.
- v1.3.0 ships live providers; the UI must let the user pick
  which one without leaving the chat tab.
- The harness philosophy is "incremental complexity matching
  feature scope" — we do not build a dynamic model registry in
  v1.2.x because nothing needs it yet.
- The `Session.model` field already exists (default
  `mock-llm/default`); a PATCH to `/api/sessions/{id}` with
  `{"model": ...}` is the only persistence path.

## Considered options

### Option A — Single model forever

No selector. The C7 `chat_stream` is hardcoded to whatever was
configured at start-up. Cheapest to ship, but blocks v1.3.0
entirely.

### Option B — Dynamic discovery from OpenRouter at startup

C1 calls `GET https://openrouter.ai/api/v1/models` at start and
populates the dropdown from the result. Requires network on
start-up and an OpenRouter API key, which contradicts the
"v1.3.0 must work offline" requirement.

### Option C (chosen) — Hardcoded list, v1.4.0 dynamic discovery

- **v1.2.x**: hardcoded single model (`mock-llm/default`); no
  selector in `ChatPanel`. `Session.model` defaults to that
  string. The v1.2.x PATCH payload accepts `model` for forward
  compatibility but no UI calls it.
- **v1.3.0**: a new `ModelRegistry` at
  `src/dhc/services/model_registry.py` exposes a hardcoded list
  of ~6 models across 3 providers (e.g. `openai/gpt-4o-mini`,
  `openai/gpt-4.1`, `anthropic/claude-3-5-sonnet`,
  `anthropic/claude-3-5-haiku`, `openrouter/auto`,
  `mock-llm/default`). C1 serves the list at `GET /api/models`.
  The `ChatPanel` gets a `<ModelSelect>` dropdown, persisted
  per-session via the existing PATCH route. A
  `provider_client_for(model_id)` factory in
  `src/dhc/integrations/__init__.py` maps model id to client.
- **v1.4.0** (future): `ModelRegistry.refresh()` calls
  OpenRouter's `/api/v1/models` to discover models at start-up,
  cached in `~/.dhc/model_cache.json` with a 24 h TTL. The
  hardcoded list remains the offline fallback.

## Decision

**Option C.** The v1.2.x surface ships without a model selector
and the v1.2.x `Session.model` field is a forward-compatibility
placeholder. v1.3.0 introduces the hardcoded list and a
`ModelRegistry`; v1.4.0 layers OpenRouter discovery on top.

## Consequences

- **Positive**: v1.2.x ships unchanged. The schema already has
  `Session.model`, so v1.3.0 needs no migration.
- **Positive**: hardcoded list in v1.3.0 means the test surface
  is deterministic; no `httpx` mocking of OpenRouter required.
- **Negative**: v1.3.0 ships with 6 models, not the ~200 a
  OpenRouter-backed discovery would expose. We document this in
  the model picker tooltip.
- **Negative**: API keys for live providers go through
  `SecretsService` (ADR-0007, to follow); the model picker and
  the secrets surface are coupled (a model is unusable without a
  matching key). v1.3.0 must surface this in the UI.
- **Risk**: if v1.4.0 ships dynamic discovery, the model id
  format must be stable across versions. We commit to the
  format `provider/model-name` (e.g. `openai/gpt-4o-mini`).

## Compliance

- The mock LLM continues to be the only "model" available in
  v1.2.x test runs.
- v1.3.0 test runs mock the provider clients (not the
  registry), so the hardcoded list is exercised end-to-end.
- `scripts/invariants_check.ps1` adds a v1.3.0 invariant:
  `ModelRegistry` is importable, the hardcoded list is non-empty,
  and every model id matches `^[a-z0-9-]+/[a-z0-9.-]+$`.
