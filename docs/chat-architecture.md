# Chat architecture (v1.2.0)

The v1.2.0 chat surface is a request/response WebSocket channel
(`/ws/chat`) on the C1 GuiWebCore, backed by a server-side session
store (`dhc.services.session_manager.SessionManager`) and a
C7 LLMStreamAdapter pointed at a local mock LLM
(`tests/fixtures/mock_llm.py`). Live LLM providers are deferred to
v1.3.0; the contract is identical.

This document is the protocol reference and the deployment
checklist. It complements `security-model.md` (which covers the
auth/secret side) and the ADRs `0004` (chat architecture) and
`0005` (markdown component).

## High-level

```
React <ChatPanel/>      WebSocket /ws/chat     Python C1 (aiohttp)
──────────────────      ──────────────────     ──────────────────
chat.send frame   ───►  origin + token guard   GuiWebCore._ws_chat_handler
                            │                      │
                            ▼                      ▼
                       auth check            SessionManager.append_message
                                                 │
                                                 ▼
                                          LLMStreamAdapter.chat_stream
                                                 │
                                                 ▼
                                          tests/fixtures/mock_llm
                                          (or v1.3.0 provider)

chat.delta/done    ◄───  send_str              (after each chunk)
```

## HTTP API (locked)

```
GET    /api/sessions                       → 200 {sessions: [{id, title, updated_at, ...}]}
POST   /api/sessions                       → 201 {id, title, ...}   body: {title?: str}
GET    /api/sessions/{id}                  → 200 {id, ..., messages: [...]} | 404
PATCH  /api/sessions/{id}                  → 200 {...}              body: {title?, pinned?, archived?, tags?, model?}
DELETE /api/sessions/{id}                  → 204  (soft-delete; body: ?hard=1 for hard-delete)
POST   /api/sessions/{id}/messages         → 201 {user_message, assistant_message}  body: {content, model?}

GET    /api/secrets                        → 200 {names: [...]}   (NEVER values)
PUT    /api/secrets/{name}                 → 204  body: {value: str}
DELETE /api/secrets/{name}                 → 204 | 404

GET    /api/llm/health                     → 200 {ok: bool, base_url: str}
```

All endpoints (except `/api/llm/health` for liveness probes) require
the loopback origin AND the per-launch bearer token. Origin must be
`http://127.0.0.1` or `http://localhost` (or `https://` of the
same); the bearer token travels in the `Authorization: Bearer`
header or `?token=` query parameter.

## WebSocket protocol (locked)

The chat WebSocket is a separate channel from the existing
`/ws` event broadcast. The two channels have distinct frame
schemas and are NOT interchangeable.

### Client → Server

| `type` | Required fields | Purpose |
|---|---|---|
| `chat.send` | `session_id`, `text` | Send a user message and stream the assistant reply |

Any other `type` is rejected with `chat.error`.

### Server → Client

| `type` | Fields | When |
|---|---|---|
| `chat.delta` | `session_id`, `delta` | Each non-empty content chunk from the LLM |
| `chat.tool_call` | `session_id`, `tool_calls: [...]` | When the LLM emits a `tool_calls` delta |
| `chat.done` | `session_id`, `tokens`, `latency_ms` | Stream complete (`finish_reason` set) |
| `chat.error` | `code`, `message?`, `session_id?` | Any failure (auth, parse, LLM, etc.) |

The server appends both the user message and the streamed
assistant turn to the session log atomically. `chat.done` is the
last frame for a given turn; the next `chat.send` starts a new
turn.

### Error codes

| `code` | Meaning |
|---|---|
| `bad_json` | The frame was not valid JSON |
| `not_object` | The frame was JSON but not an object |
| `unknown_type` | The `type` field is not `chat.send` |
| `missing_session_or_text` | Required fields are empty |
| `no_session_manager` | Server-side misconfiguration |
| `session_not_found` | `session_id` does not exist |
| `no_llm` | No LLM adapter is wired in |
| `llm_failed` | The LLM call raised an exception |
| `unauthorized` | (HTTP 401 during handshake) Wrong/missing token |
| `forbidden` | (HTTP 403 during handshake) Wrong origin |

## Session lifecycle

1. Client calls `POST /api/sessions` with an optional `title`.
2. The server returns a `Session` with an `id` (e.g. `s_<16 hex>`),
   `created_at`, `updated_at`, and an empty `messages` list.
3. Client connects to `/ws/chat?token=<token>` and sends
   `chat.send` frames.
4. The server appends each user message to the session, runs the
   LLM, streams `chat.delta` chunks, and on `finish_reason` writes
   a single `assistant` message with the full reply.
5. The client may `PATCH` the session at any time to rename, pin,
   archive, tag, or change the model. The model is per-session and
   travels in `Session.model`.
6. The client may `DELETE` to soft-archive (default) or hard-delete
   (`?hard=1`). Soft-archived sessions are excluded from the
   default list but still present on disk.
7. On restart, the server reads the JSON files in
   `~/.dhc/sessions/` and re-hydrates the in-memory cache lazily.

## Storage layout

```
~/.dhc/
├── sessions/
│   ├── s_<id>.json
│   └── s_<id>.json.tmp   (only during atomic write)
├── sessions-index.json    (lightweight index of all session ids)
├── secrets/
│   ├── secrets.key        (32-byte master key, mode 0o600)
│   └── secrets.log        (append-only JSONL of op | name | blob)
└── ...
```

Every write is atomic: the server writes to `*.tmp` and `os.replace`s
it onto the target. On a crash mid-write the original file is
intact.

## Mock LLM (v1.2.0)

`tests.fixtures/mock_llm.py` is an aiohttp server that:

- `GET /healthz` → `{"ok": true}`
- `POST /v1/chat/completions` (OpenAI-compatible) → SSE stream
- `GET /v1/stream/{scenario}` (legacy v1.1.0 surface)

It supports scenarios selected via the JSON body's `model` field:

- `default` — generic LLM-style reply that quotes the user text
- `echo`    — echoes the user text verbatim
- `code`    — returns a Python code block
- `tool`    — emits one `tool_calls` delta, then text
- `slow`    — sleeps 20 ms between chunks (for streaming UX tests)
- `long`    — ~3 KB reply (for truncation tests)

The mock is the v1.2.0 test surface; CI runs against it. v1.3.0
will swap the URL for a real provider.

## Threat model recap

- The loopback bind is the primary defense. No outbound network
  is required for v1.2.0; the mock is on `127.0.0.1`.
- The per-launch bearer token (256 bits, `secrets.token_urlsafe`)
  is the secondary defense. The token is generated at start, written
  to a `chmod 600` file, and embedded in the served `index.html`
  via a `<meta name="dhc-token">` tag. The browser reads it on
  page load and sends it in the WS query string.
- API keys (when v1.3.0 lands) are stored encrypted at rest in
  `~/.dhc/secrets/secrets.log` under an HMAC-SHA256-authenticated
  envelope. See `secrets-model.md`.
- HTML rendering is centralized in `components/Markdown.tsx`. The
  PowerShell invariant script asserts no other file in the React
  build calls `dangerouslySetInnerHTML` or imports the sanitizers
  directly.

## Retry strategy

v1.2.x does not implement retry. The local mock LLM at
`tests/fixtures/mock_llm.py` is loopback-only and effectively
cannot fail in a way that would benefit from retry, so adding
retry would be speculative complexity. The C1 `/ws/chat` handler
surfaces the first error it sees and closes the socket with
code `1011` (server error).

v1.3.0 will add a single retry policy for the **live-provider**
path (not the mock path):

- 3 attempts total (1 initial + 2 retries).
- Backoff: 1 s, 2 s. Linear, not exponential — the per-call
  latency budget for chat is already dominated by LLM time.
- Retry **only** on HTTP 5xx and connection errors
  (`aiohttp.ClientConnectionError`, `asyncio.TimeoutError`).
- Do **not** retry on:
  - HTTP 4xx (client errors, including 400 / 401 / 403 / 404).
    The request will not succeed on retry without code change.
  - HTTP 408 (request timeout) — already on the wire too long.
  - HTTP 429 (rate limited) — handled by the existing
    `plugins/rate_limiter_v1` token bucket, which yields cleaner
    backpressure than a retry loop.

The retry policy is implemented in the new provider clients
under `src/dhc/integrations/`, **not** in C7, so the streaming
buffer guarantees in C7 stay unchanged. The provider client
returns the final attempt's response and a `retries_used: int`
field on the result so the UI can show a "delayed, retried"
indicator if needed.

## Smoke checklist

The live smoke in `tests/chat/smoke_v12.py` (run with `python
tests/chat/smoke_v12.py`) verifies 19 checks end-to-end:

- /healthz, /api/llm/health
- /api/sessions CRUD
- /api/sessions/{id}/messages round-trip
- /api/secrets CRUD (and that values are NEVER leaked in /api/secrets)
- /ws/chat send/delta/done flow
- on-disk persistence of sessions and secrets
