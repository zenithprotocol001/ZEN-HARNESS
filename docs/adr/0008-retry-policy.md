# ADR-0008 — Retry Policy

- **Status**: Accepted (2026-09-02)
- **Deciders**: harness maintainers
- **Date**: 2026-09-02
- **Supersedes**: (none)
- **Related**: ADR-0009 (Provider Abstraction), `docs/chat-architecture.md` ("Retry strategy" section)

## Context and problem statement

v1.3.0 introduces live LLM providers. The network is a new failure surface — transient 5xx, dropped connections, timeouts. The user-facing chat WS handler must stay responsive during retries and surface the final failure cleanly. We need to lock:

- The number of attempts (and the backoff schedule between them).
- Which error classes trigger a retry.
- Which error classes do not.
- Whether the user sees the retry happening.
- Where the retry loop lives (in the provider client, in C7, or in a shared utility).

## Decision drivers

- Per-call latency is dominated by LLM time (1-10 s for a typical completion). A 2 s backoff between retries is in the noise.
- The existing `rate_limiter_v1` plugin (SHA-pinned, unchanged since v1.1.0) already handles 429 backpressure. Retrying 429s in the provider client would double-handle the case.
- The 4xx class is a "do not retry" signal: the request is malformed or the API key is wrong, and retrying will not help.
- The 5xx class (and Anthropic's 529 "overloaded") is a "transient" signal: retrying usually works.

## Considered options

### Option A — Retry inside C7

C7's `chat_stream` would know about retry math. But C7 is shared by the mock LLM and the v1.2.x GET-streaming path; baking retry into C7 conflates the streaming and the chat surfaces.

### Option B — Retry inside each provider client (chosen)

Each `LLMProvider` subclass owns its retry loop. The retry config is injected. C7's wrapper is a thin dispatcher: it resolves the model and key, hands off to the client, and surfaces the final result. The mock LLM has no retry; the v1.2.x path is untouched.

### Option C — Shared utility module

A `dhc.integrations.retry` utility that wraps any async iterator. Pros: DRY. Cons: hides control flow; harder to test the per-client edge cases (e.g. Anthropic's 529).

## Decision

**Option B.** The retry loop lives in `LLMProvider.chat_stream` of each concrete client. The `RetryConfig` is a frozen dataclass injected as a parameter.

Defaults (locked by this ADR):

- `max_attempts = 3` (1 initial + 2 retries).
- `backoff_seconds = (1.0, 2.0)` (linear; the 3rd attempt is the last so an exponential curve would be identical here).
- `retry_on_5xx = True`.
- `retry_on_network_error = True`.
- `retry_on_4xx = False`.
- `connect_timeout_s = 5.0`, `read_timeout_s = 30.0`.

Status code mapping:

- `200` → success, stream deltas.
- `4xx` (any) → `ProviderError(status=..., message="4xx error: {status}")` after 1 attempt. The chat WS closes with `1011`.
- `408` (Request Timeout) → `ProviderError` after 1 attempt. Same as 4xx.
- `429` (Too Many Requests) → `ProviderError` after 1 attempt. The `rate_limiter_v1` plugin is the right place to back this off; double-handling is worse than a clear error.
- `5xx` (any, including 500/502/503/504) → retry up to `max_attempts`. Final `ProviderError` on exhaustion.
- Anthropic-specific `529` (Overloaded) → retry (treated as 5xx).
- Network errors (`aiohttp.ClientConnectionError`, `aiohttp.ClientPayloadError`, `asyncio.TimeoutError`) → retry. The backoff applies between attempts.
- Other exceptions (e.g. `json.JSONDecodeError` in SSE parsing, `KeyError` in unexpected response shape) → `ProviderError` after 1 attempt; these are programmer errors, not transient failures.

The retry loop is **silent** to the user. No `chat.delta` frame is sent for a failed attempt. The user sees a single chat turn that may take 3× the normal LLM latency in the worst case, but the spinner is already there to cover the wait. The first retry frame is sent only when an attempt succeeds.

## Consequences

- **Positive**: retry logic is testable per-client (10 OpenAI tests, 10 Anthropic tests, 5 OpenRouter tests cover the policy).
- **Positive**: the C7 wrapper stays thin; the v1.2.x mock path is unaffected.
- **Positive**: a `RetryConfig` field is exposed for tests, so a test can drop `backoff_seconds=(0.01, 0.01, 0.01, 0.01)` and run the retry path in <50 ms.
- **Negative**: 3 copies of the retry loop (one per client) duplicate ~20 lines of code. We accept this for testability; the alternative (Option C) hides the control flow.
- **Negative**: the user has no explicit "retrying" indicator. Acceptable because the spinner covers the wait; can be revisited in v1.3.1 if users complain.
- **Risk**: an API call that takes 10 s on a 5xx means the user waits 10 s + 1 s backoff + retry latency. The 30 s read timeout in `RetryConfig` caps the worst case at ~50 s for a fully failing call. If the user wants faster failure, v1.3.1 can expose `max_attempts` in the chat panel config.

## Compliance

- `RetryConfig` is a frozen `@dataclass(frozen=True)`. Mutating a field raises `FrozenInstanceError`. Test: `test_retry_config_is_frozen`.
- Every provider client must respect the same defaults. The integration test `test_c7_dispatch.py::test_c7_ws_chat_retry_invisible_to_client` asserts the user-facing behavior end-to-end.
- The C7 wrapper does **not** override `RetryConfig` defaults; tests pass custom configs only when they need to shorten the backoff.
- A new invariant in `scripts/invariants_check.ps1` asserts that every `LLMProvider` subclass has a `test_*_retry_*` test in its test file. This is enforced by listing the test files in the invariant and checking for the substring.
