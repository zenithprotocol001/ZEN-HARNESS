# ADR-0001 — Three-tab web UI with HTML injection localized to the events panel

- **Status:** Accepted
- **Date:** 2026-09-01
- **Authors:** DHC maintainers

## Context

The original `App.tsx` had grown past 200 lines and was responsible for:

1. WebSocket subscription and bearer-token extraction from a `<meta>` tag.
2. Live event list rendering (the only place the web client injects HTML).
3. Module health dots.
4. Plugin load/unload affordances.
5. A prompt browser.

Auditors who wanted to answer "where exactly does the harness inject
HTML?" had to read the whole file. The single-file structure made it
too easy to introduce an XSS by adding a second `dangerouslySetInnerHTML`
call in an unrelated feature.

## Decision

Split the client into:

- `App.tsx` — router + WS subscriber + tab state. No HTML rendering.
- `panels/ModulesPanel.tsx` — module/plugin grid + paste-and-score.
- `panels/EventsPanel.tsx` — the **only** file that calls
  `renderMarkdown`, `renderToolResult`, or `dangerouslySetInnerHTML`.
- `panels/PromptsPanel.tsx` — 10 master prompts.
- `components/ModuleCard.tsx` — shared card UI.

`scripts/invariants_check.ps1` now asserts both:

- Positive: `EventsPanel.tsx` contains the three rendering calls.
- Negative: `App.tsx` does **not** contain any of them.

Any regression that pulls HTML rendering back into `App.tsx` fails the
invariants.

## Consequences

- One file owns HTML injection. Future XSS fixes are localized.
- The negative invariant is a structural guardrail that survives
  careless refactors.
- The build emits a single 217 KB JS bundle (71 KB gzipped); no
  runtime cost from the split.
- A new panel is a small additive change: one file under
  `apps/web/src/panels/` + one row in `App.tsx`'s tab list.
- Negative invariants are a small cultural cost: contributors who
  "just want to add a button" cannot accidentally inject HTML
  anywhere except the events panel.
