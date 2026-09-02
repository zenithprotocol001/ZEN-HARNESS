# ADR 0005 — Markdown component as the single XSS guardrail

- **Status:** Accepted (v1.2.0)
- **Date:** 2026-09-02
- **Deciders:** Rex

## Context and problem statement

The v1.1.0 React client renders HTML payloads (event payloads,
tool results, assistant markdown) via `dangerouslySetInnerHTML`.
The XSS defense is layered: the Python bridge sets a strict CSP
that forbids `'unsafe-inline'` and `'unsafe-eval'`; the React
client passes every payload through a DOMPurify wrapper
(`renderMarkdown` / `renderToolResult` in `sanitize.ts`).

In v1.1.0 the rendering was concentrated in
`apps/web/src/panels/EventsPanel.tsx`, so the PowerShell
invariant script could assert that `dangerouslySetInnerHTML`
appeared only in that file.

v1.2.0 adds a `ChatPanel` that ALSO renders markdown (the
assistant message bubbles). If the rendering logic is
duplicated in `ChatPanel.tsx`, the invariant must be relaxed
to allow two files — and any future panel that wants to render
HTML must be added to the allow-list. This is a maintenance
burden and a security risk (a forgotten allow-list entry would
let a new file call the sanitizer with a custom config).

## Decision drivers

- The XSS guardrail must remain "one line, one place" so a
  security review can audit it by reading a single file.
- The CSP + DOMPurify pattern is well-tested and must not
  regress.
- The new chat panel must be able to render markdown without
  duplicating the `dangerouslySetInnerHTML` site.

## Considered options

### Option A — Inline the rendering in each panel

`EventsPanel.tsx` and `ChatPanel.tsx` each call
`dangerouslySetInnerHTML` directly with `renderMarkdown`.

Pros: simple. Cons: the invariant is now a 2-file allow-list
that grows with every new panel. A future contributor who adds
a 3rd panel with a custom (looser) DOMPurify config could
silently bypass the guardrail.

**Rejected** — directly contradicts the v1.1.0 design goal of
"one place to audit."

### Option B — Extend the DOMPurify config per call site

Each panel picks its own `ALLOWED_TAGS` / `FORBID_TAGS`.

Pros: flexible. Cons: increases the surface of the security
review; the reviewer must check every call site, not just one
file.

**Rejected** — same as A, worse.

### Option C — Extract a `<Markdown />` and `<ToolResult />` component (chosen)

A new file `apps/web/src/components/Markdown.tsx` owns the
`dangerouslySetInnerHTML` call AND the call to the sanitizer.
Both `EventsPanel` and `ChatPanel` import the component. The
invariant script asserts that no other `.tsx` file contains
`renderMarkdown`, `renderToolResult`, or
`dangerouslySetInnerHTML`.

Pros: the security-critical line is in exactly one file. The
invariant is a deny-list (default-deny) instead of an
allow-list. Adding a new panel that wants to render HTML
requires an explicit decision (import the component or call
the sanitizer with a justification).

## Decision

Adopt Option C. The new file is
`apps/web/src/components/Markdown.tsx`. The invariant script
in `scripts/invariants_check.ps1` adds a negative-invariant
loop that scans a fixed list of files
(`App.tsx`, all panels, all components) and asserts that
neither `renderMarkdown` nor `renderToolResult` nor
`dangerouslySetInnerHTML` appears in any of them.

The new component exports two wrappers:

```tsx
<Markdown source={text} />          // → renderMarkdown(text)
<ToolResult text={text} />          // → renderToolResult(text)
```

Both call `dangerouslySetInnerHTML` with the string returned by
the corresponding sanitizer. There is no other way to inject
HTML in the React client.

## Consequences

Positive:

- The XSS guardrail is auditable by reading one file
  (`components/Markdown.tsx`) and one sanitizer module
  (`sanitize.ts`).
- The invariant is a default-deny deny-list. Any future
  contributor who calls `dangerouslySetInnerHTML` in a new
  component is caught at CI time, not in a security review.
- The CSP and DOMPurify defense-in-depth pattern is unchanged.

Negative:

- The component is a thin wrapper; some readers may find it
  over-engineered. The trade-off is explicit: the
  maintainability cost of one extra file is lower than the
  audit cost of checking every panel.

## Compliance

- `tests/security/test_c1_xss.py` was updated to scan
  `components/Markdown.tsx` (instead of `panels/EventsPanel.tsx`).
- `scripts/invariants_check.ps1` adds the deny-list loop.
- The `// 06 — THE WEB CLIENT` and `// 17 — 3-TAB WEB UI`
  sections of `README.md` were updated to describe the new
  component.
