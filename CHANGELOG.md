# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.3.0] - 2026-09-02

### Added

- **`dhc.integrations.base.LLMProvider`** (ADR-0009): abstract base
  for live LLM providers. Frozen `RetryConfig` dataclass (3 attempts,
  1 s/2 s linear backoff per ADR-0008) and `ProviderError` exception
  with `status` / `provider` / `model` context.
- **`dhc.services.model_registry.ModelRegistry`** (ADR-0006):
  hardcoded list of 6 models across 4 providers (mock, openai,
  anthropic, openrouter). Frozen `Model` dataclass with id, name,
  provider, context_length, pricing, capabilities (frozenset).
- **3 concrete provider clients** (ADR-0009):
  - `OpenAIClient` — POSTs to `https://api.openai.com/v1/chat/completions`,
    OpenAI SSE parsing (deltas + tool_calls).
  - `AnthropicClient` — POSTs to `https://api.anthropic.com/v1/messages`,
    Anthropic SSE parsing (message_start / content_block_delta /
    message_delta / message_stop; treats 529 as 5xx for retry).
  - `OpenRouterClient` — POSTs to `https://openrouter.ai/api/v1/chat/completions`,
    OpenAI-compatible (reuses the OpenAI SSE parser).
- **`provider_client_for(model)` factory** in
  `src/dhc/integrations/__init__.py`: dispatches by
  `model.provider`, raises `ProviderError` for unknown or mock.
- **C7 dispatch seam**: `LLMStreamAdapter` now accepts optional
  `model_registry` and `secrets_service` kwargs. When both are set
  and the model is not the mock, the adapter resolves the provider
  via the factory, looks up the API key from `SecretsService` using
  the `llm_provider_{provider}_{model_id}` naming convention
  (ADR-0007), and streams from the live client. The v1.2.x
  mock-only path is preserved when either arg is missing.
- **C1 routes**:
  - `GET /api/models` — list all 6 models.
  - `GET /api/models/{id}` — fetch a single model (uses
    `add_get("/api/models/{id:.+}", ...)` to allow slashes in the
    id like `openai/gpt-4o-mini`).
- **`<ModelSelect>` React component** in
  `apps/web/src/components/ModelSelect.tsx`: fetches
  `GET /api/models`, groups options by provider in `<optgroup>`,
  and calls `onChange(model_id)` on selection. Wired into
  `ChatPanel` header; persists selection via PATCH
  `/api/sessions/{id}` with `{"model": ...}`.
- **3 new docs**: `docs/v1.3.0-technical-spec.md`,
  `docs/v1.3.0-test-plan.md`, plus the 3 ADRs below.
- **3 new ADRs**: `0007-api-key-management.md`,
  `0008-retry-policy.md`, `0009-provider-abstraction.md`.

### Changed

- `serve_c1.py` now constructs a single `ModelRegistry` and a
  `SecretsService` (when `--secrets-dir` is set) eagerly, then
  passes both to the C7 plugin's apply config so live dispatch
  works at startup.
- The invariant script `scripts/invariants_check.ps1` adds 4 new
  checks: `<ModelSelect>` exists, fetches `/api/models`, is
  imported by `ChatPanel`, and `ChatPanel` PATCHes the session
  with the selected model id.

### Security

- Live provider keys are stored in `SecretsService` (HMAC-SHA256
  encrypt-then-MAC envelope, unchanged from v1.2.0). The keys
  never appear in any `StreamChunk` yielded to the consumer
  (covered by `test_chat_stream_redacts_key_in_logs` and the
  Anthropic/OpenRouter equivalents).
- The `<ModelSelect>` component does NOT collect API keys; the
  Settings modal is deferred to v1.3.1 per the v1.3.0 scope.
- The `StreamChunk` shape is frozen from v1.2.0; no new fields
  are added in v1.3.0 (per ADR-0009 § "Consequences" — `usage`
  is deferred to v1.3.1).

### Deferred to v1.3.1 (explicit)

- Per-secret random nonce in the envelope format (per
  `docs/secrets-model.md` Salt strategy section).
- Settings modal for API key entry.
- Model config menu (temperature / max_tokens / top_p).
- `StreamChunk.usage` field for token counting.
- Conversation branching, attachments, token counter, context-window
  progress bar.

### Tests

- **388 passed, 2 skipped, 1 xpassed** (was 318 in v1.2.1).
- +70 new tests in v1.3.0:
  - 15 `tests/integrations/test_model_registry.py`
  - 7 `tests/integrations/test_base.py` (RetryConfig + ProviderError + LLMProvider)
  - 10 `tests/integrations/test_openai_client.py`
  - 10 `tests/integrations/test_anthropic_client.py`
  - 5 `tests/integrations/test_openrouter_client.py`
  - 5 `tests/integrations/test_factory.py`
  - 8 `tests/chat/test_model_routes.py` (C1 /api/models routes)
  - 10 `tests/chat/test_c7_dispatch.py` (C7 dispatch + WS round-trip)
- All v1.2.x tests pass without modification.
- v1.2.0 chat smoke (19/19) still passes.

## [1.2.1] - 2026-09-02

### Added

- **`SessionManager.search(query, limit, include_archived)`**:
  case-insensitive full-text search across session title and
  message content, returning full `Session` objects ordered by
  `pinned desc, updated_at desc`. Exposed at
  `GET /api/sessions?q=...` on C1 (the existing `?search=`
  parameter is unchanged and still returns summaries via
  `list_summaries`). Empty / whitespace queries return `[]` so
  callers fall back to `list_summaries` for the full listing.
- **Doc: Salt strategy** (`docs/secrets-model.md`): explains why
  v1.2.x uses a fixed scrypt salt under the single-tenant
  threat model, and reserves headroom for a per-secret random
  nonce in v1.3.0.
- **Doc: Retry strategy** (`docs/chat-architecture.md`):
  documents that v1.2.x has no retry (mock LLM is loopback)
  and commits v1.3.0 to a 3-attempt, 1 s/2 s backoff policy
  on 5xx + connection errors only.
- **ADR-0006 — Model Selection Strategy**: locks the
  v1.2.x → v1.3.0 → v1.4.0 progression (single mock → hardcoded
  list of ~6 → OpenRouter dynamic discovery).

### Changed

- `GET /api/sessions?q=...` now returns matches via the new
  `SessionManager.search()` instead of the v1.2.0 `list_summaries`
  filter. The legacy `?search=...` parameter is preserved for
  backward compatibility.

### Tests

- 318 passed, 2 skipped, 1 xpassed (was 314 in v1.2.0).
- +3 unit tests in `tests/chat/test_session_manager.py`:
  `test_search_by_title`, `test_search_by_message_content`,
  `test_search_empty_query_returns_empty`.
- +1 e2e test in `tests/chat/test_chat_ws.py`:
  `test_sessions_q_alias_full_text_search` (covers title match,
  content match, case-insensitivity, no-match, and empty-q).

### Compatibility

- No plugin SHA changes; no scoring formula change; no module
  contract change.
- v1.2.0 zip is superseded; v1.2.1 is a drop-in replacement.

## [1.2.0] - 2026-09-02

### Added

- **4-tab web UI**: Modules, Events, Prompts, Chat. The new
  `ChatPanel` is in `apps/web/src/panels/ChatPanel.tsx`; the
  left-rail `SessionList` in `panels/SessionList.tsx`; the
  Ctrl+K search in `components/SearchOverlay.tsx`.
- **Markdown component guardrail** (ADR 0005): the new
  `apps/web/src/components/Markdown.tsx` is the **only** file in
  the React client that calls `dangerouslySetInnerHTML` or
  imports the sanitizers. The PowerShell invariant script scans
  every `.tsx` file and asserts a deny-list (default-deny).
  This makes the XSS guardrail a single auditable line of code.
- **Server-side chat sessions** (`dhc.services.session_manager.SessionManager`):
  persistent JSON files under `~/.dhc/sessions/`, atomic writes
  via `os.replace`, search by title or message content, soft
  delete (archived) and hard delete, 1000-message cap with
  oldest-first truncation.
- **Encrypted secret store** (`dhc.cordis.secrets.SecretsService`):
  append-only JSONL log at `~/.dhc/secrets/secrets.log`,
  per-user 32-byte master key in `secrets.key` (mode 0o600),
  encrypt-then-MAC envelope using HMAC-SHA256 counter-mode
  keystream and a separate HMAC-SHA256 tag. `GET /api/secrets`
  returns names only — values are never returned. v1.2.0 stages
  these secrets for v1.3.0's live providers.
- **C7 chat_stream extension**: a new `chat_stream(messages, model)`
  method on `LLMStreamAdapter` that POSTs to
  `{base_url}/v1/chat/completions` (OpenAI-compatible) and
  yields `StreamChunk` deltas. The existing `stream()` method
  is unchanged.
- **C1 chat + session + secret routes**:
  - `WS /ws/chat` — request/response channel with a distinct
    frame schema (see `docs/chat-architecture.md`).
  - `GET/POST /api/sessions`, `GET/PATCH/DELETE /api/sessions/{id}`,
    `POST /api/sessions/{id}/messages`.
  - `GET /api/secrets`, `PUT/DELETE /api/secrets/{name}`.
  - `GET /api/llm/health`.
- **Mock LLM fixture** (`tests/fixtures/mock_llm.py`): an
  aiohttp server with `POST /v1/chat/completions` (OpenAI-compatible)
  and `GET /v1/stream/{scenario}` (legacy). Scenarios:
  `default`, `echo`, `code`, `tool`, `slow`, `long`. Loopback
  only; no network.
- **Smoke test runner** (`tests/chat/smoke_v12.py`): 19
  end-to-end checks against the mock LLM. Run with
  `python tests/chat/smoke_v12.py`.
- **New docs**: `docs/chat-architecture.md`, `docs/session-storage.md`,
  `docs/secrets-model.md`.
- **New ADRs**: `0004-chat-and-sessions.md`, `0005-markdown-component.md`.

### Changed

- `App.tsx` is now a 4-tab router (was 3).
- `EventsPanel.tsx` no longer calls `dangerouslySetInnerHTML`
  directly; it imports `components/Markdown.tsx`.
- `tests/security/test_c1_xss.py` was updated to scan
  `components/Markdown.tsx` (was `panels/EventsPanel.tsx`).
- `scripts/invariants_check.ps1` adds a deny-list loop that
  scans 9 fixed `.tsx` files for `renderMarkdown`,
  `renderToolResult`, and `dangerouslySetInnerHTML`.

### Test counts

- 314 passing, 2 skipped, 1 xpassed (was 242 passing, 2 skipped,
  1 xpassed in v1.1.0).
- 100+ invariants (was 92 in v1.1.0).
- 5 new test files under `tests/chat/` (secrets, sessions,
  mock LLM, C1 chat routes, smoke).
- Scorer DHC-V still 100.0.

## [1.1.0] - 2026-09-01

### Added

- **3-tab web UI**: Modules, Events, Prompts. `apps/web/src/App.tsx` is
  now a router; the rendering code lives in `panels/EventsPanel.tsx`.
  `apps/web/src/components/ModuleCard.tsx` is the shared card.
- **Plugin marketplace**: 5 bundled plugins under
  `src/dhc/plugins/`:
  - `rate_limiter_v1` — per-agent-event throttle
  - `session_exporter_v1` — snapshot the C2 session log to JSONL
  - `model_router_v1` — pick a backend C7 by prompt prefix
  - `memory_store_v1` — key/value store on the context
  - `prompt_browser_v1` — `/prompts` and `/prompts/{key}` routes
- **Manifest integrity**: every plugin ships `manifest.json` with
  `sha256`; loader verifies with `hmac.compare_digest` at load time.
- **C1 marketplace routes**:
  - `GET /api/manifest` — full manifest (modules + plugins)
  - `GET /plugins` — discovered vs loaded
  - `POST /plugins/{id}` — load
  - `DELETE /plugins/{id}` — unload
  - `GET /prompts` — list 10 master prompts (requires
    `prompt_browser_v1`)
  - `GET /prompts/{key}` — single prompt body
  - `POST /api/eval` — offline in-proc eval of pasted code
- **Paste-and-score** in the browser: paste an LLM response, pick the
  target module, get a DHC-V back.
- **Tests**: 242 passing (was 190), with 34 new tests in
  `tests/plugins/`.
- **Invariants**: 92 passing, including panel-based positive and
  negative invariants for the new 3-tab UI.
- **Docs**: `docs/README.md`, `docs/architecture.md`,
  `docs/security-model.md`, `docs/plugin-authoring.md`,
  `docs/SHA-PINNING.md`, `docs/CHANGELOG.md`, `docs/CONTRIBUTING.md`,
  3 ADRs under `docs/adr/`, `GLOSSARY.md`, `relay/MANIFEST.txt`.
- **Relay exclusion list** in `scripts/package_relay.ps1` keeps
  runtime logs and bearer tokens out of the zip.

### Changed

- `README.md` synced to v1.1.0 (was v0.6.0 in the banner).
- `App.tsx` split; the 3 previous rendering helpers
  (`renderMarkdown`, `renderToolResult`, `dangerouslySetInnerHTML`)
  moved to `panels/EventsPanel.tsx`. Invariants now assert both
  positive (EventsPanel has them) and negative (App.tsx does not).
- `apps/web/src/sanitize.ts` now also exports `formatPayload` and
  `isToolResult` (formerly inline in `App.tsx`).
- Plugin SHA-256 values locked in `docs/SHA-PINNING.md` and asserted
  by `scripts/invariants_check.ps1`.

### Security

- 0 critical, 0 high, 0 medium, 0 low findings on the reference
  implementation. DHC-V = 100.0 (production_ready).

## [1.0.0] - 2026-09-01

### Added

- 10 core modules C1–C10.
- Cordis port at `src/dhc/cordis/`.
- 190 passing tests (was 27); baseline restoration.
- 71 static invariants.
- `dhc-v-report.json` self-score: 100.0.

## [0.6.0] - 2026-09-01

### Added

- Initial 3-module / 4-module reference implementation.
- 19 test files, 7 invariants.
- `dhc-v-report.json` self-score: 100.0.
