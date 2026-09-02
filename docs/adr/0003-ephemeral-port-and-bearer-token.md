# ADR-0003 — Ephemeral port and per-launch bearer token

- **Status:** Accepted
- **Date:** 2026-09-01
- **Authors:** DHC maintainers

## Context

The C1 GuiWebCore needs to serve the React app and accept WebSocket
connections from the browser. Two design choices were open:

1. **Port**: fixed (e.g. `3080`) vs. ephemeral (OS picks a free one).
2. **Auth**: none vs. static token vs. per-launch token.

### Why not a fixed port?

A fixed port collides with other dev tools. It is also visible in
screenshots and stack traces, which makes it a soft target for
automated scanners even though the harness only binds to loopback.

### Why a token at all?

A loopback-only HTTP server is still reachable by any other process
on the same machine. On a multi-user host, this is a leak. The
threat model is "single-user dev box, multi-process trust boundary"
— so we want **some** authentication even on loopback.

### Why a per-launch token, not a static one?

A static token leaks through the dev lifecycle (it ends up in shell
history, dotfiles, screenshots, bug reports). A per-launch token is
wiped on stop and only valid for the current process lifetime.

## Decision

When C1 starts:

1. Pick a free ephemeral port. Write it to `serve_c1.port`.
2. Generate a 256-bit token via `secrets.token_urlsafe(32)`. Write
   it to `serve_c1.token`.
3. Embed the token in the served `index.html` as
   `<meta name="dhc-token" content="...">`.
4. Bind to `127.0.0.1` only. Reject any other origin.
5. Validate the token on every HTTP route via
   `Authorization: Bearer <token>` or `?token=<token>` query
   parameter.
6. Validate the token on the WS handshake. Reject mismatches with
   close code `1008`.

A `--no-token` opt-out is provided for tests that want to skip auth,
but production C1 always runs with auth on.

The CSP header is strict:

```
default-src 'self'; script-src 'self'; style-src 'self';
img-src 'self' data:; connect-src 'self' ws: wss:;
object-src 'none'; frame-ancestors 'none'; base-uri 'self'
```

No `unsafe-inline`, no `unsafe-eval`, no wildcards.

## Consequences

- Any process on the same machine can connect to the loopback port
  if it can read `serve_c1.token`. This is the documented threat
  boundary.
- Any process **without** the token gets a `401` (or WS close `1008`).
- The token is never written to a log, never serialized into an
  event payload, and never sent over the network.
- The port and token are ephemeral; restarting C1 invalidates both.
- The `<meta name="dhc-token">` injection is a **trust assumption**.
  A compromised bridge can deliver any token it wants. This is the
  same trust model as a static `<script>` tag in a single-page app.
- Tests use `aiohttp.test_utils` and do not require the token; C1's
  `require_token` is off in the test harness.
