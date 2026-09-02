# ADR 0004 — Multi-tab chat with server-side sessions

- **Status:** Accepted (v1.2.0)
- **Date:** 2026-09-02
- **Deciders:** Rex

## Context and problem statement

The v1.1.0 web client has three tabs (Modules, Events, Prompts)
but no chat surface. The user wants a v1.2.0 release that adds
LLM-driven chat with session persistence, search, and a
session list. The v1.1.0 plugin marketplace is the closest
precedent: server-side state, HTTP API + WebSocket events,
per-launch auth.

The chat surface has two distinct concerns that should NOT be
mixed:

1. **Session state** — durable, per-user, persisted to disk.
   A chat has a title, a list of messages, model selection, and
   metadata (pinned, archived, tags).
2. **Live streaming** — ephemeral, per-connection, low-latency.
   The assistant reply arrives as a sequence of deltas over a
   WebSocket.

How should these two surfaces be modeled in the protocol, the
server, and the React client?

## Decision drivers

- The loopback-only threat model must be preserved.
- The CSP and the XSS guardrail must not regress.
- Live LLM calls are out of scope for v1.2.0; the chat path must
  work end-to-end against the local mock so v1.3.0 can drop in
  a real provider.
- The existing C7 stream adapter (SSE consumer) should be reused,
  not duplicated.

## Considered options

### Option A — Single tab, no session persistence

A "chat" tab that talks to the LLM but does not save anything.
Pros: smallest surface. Cons: no history, no resume, no search.
**Rejected** — the user's stated requirements are persistent
sessions.

### Option B — Client-side persistence only

Sessions are stored in `localStorage`; the server is stateless.
Pros: no server storage. Cons: tied to one browser; no
search across machines; no shared encryption at rest; cannot
back up the chat log; cannot move sessions to a different
machine. **Rejected** — the harness already has a server-side
plugin marketplace, so the precedent is server-side state.

### Option C — Server-side persistence + dedicated chat WS (chosen)

- HTTP `REST` for CRUD on sessions (`/api/sessions[/...]`).
- HTTP `REST` for CRUD on secrets (`/api/secrets[/...]`).
- A separate WebSocket `/ws/chat` for the request/response
  streaming flow.
- A local mock LLM (`tests/fixtures/mock_llm.py`) so the
  end-to-end path is testable without network.

Pros: each concern is owned by a small, well-tested surface;
the existing C1 routes and the new chat routes share the same
origin/bearer-token guard; the chat WS schema is independent
of the event-broadcast WS, so the existing 3-tab UI keeps
working.

## Decision

Adopt Option C. The chat surface is added in three places:

1. **Server** — `dhc.services.session_manager.SessionManager`
   for persistence; new C1 routes for sessions/secrets/LLM
   health; a new `/ws/chat` handler.
2. **React client** — a 4th `ChatPanel` tab, a left-rail
   `SessionList` (Today/Yesterday/Older grouping), a Ctrl+K
   `SearchOverlay`, and a message bubble that renders markdown
   through the new `components/Markdown.tsx` component.
3. **Mock LLM** — `tests/fixtures/mock_llm.py` exposing
   `POST /v1/chat/completions` with SSE responses for the
   `default`/`echo`/`code`/`tool`/`slow`/`long` scenarios.

## Consequences

Positive:

- The two WS channels (`/ws` for event broadcast, `/ws/chat`
  for chat) have distinct frame schemas. A malformed chat
  frame cannot crash the event bus.
- The mock LLM is the v1.2.0 test surface. CI runs the smoke
  in `tests/chat/smoke_v12.py` (19 checks) against the mock.
- The session log is a flat directory of JSON files; backup
  and restore are trivial.
- The chat UI is the same React app, served by the same C1
  process, behind the same CSP.

Negative:

- The chat surface is now a non-trivial piece of the harness
  and must be maintained alongside the existing 10 modules.
- The mock LLM is a test fixture that must be kept in sync
  with the OpenAI chat-completions shape, or v1.3.0 will
  inherit a mismatch.

## Compliance

- Origin guard: same `_is_allowed_origin` check as `/ws`.
- Bearer token: same `_check_token` helper.
- CSP: unchanged (no new inline scripts or styles).
- XSS guardrail: HTML rendering is centralized in
  `components/Markdown.tsx`; the PowerShell invariant script
  scans every `.tsx` file and asserts that no other component
  imports the sanitizers or calls `dangerouslySetInnerHTML`.
