# DHC Architecture

The DHC benchmark is a **hermetic, secure, mathematically rigorous** reference
implementation of the [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
runtime, ported from TypeScript to Python, with a Cordis-style plugin model.

## Layers

```
┌────────────────────────────────────────────────────────────────────┐
│  L7  Web client (apps/web)                                         │
│      React + Vite + DOMPurify + marked                             │
│      3 tabs: Modules, Events, Prompts                              │
├────────────────────────────────────────────────────────────────────┤
│  L6  C1 GuiWebCore (src/dhc/modules/c1_gui_web_core)               │
│      aiohttp + bearer token + CSP + WS bridge                      │
│      Marketplace routes: /plugins, /prompts, /api/eval, /healthz   │
├────────────────────────────────────────────────────────────────────┤
│  L5  Cordis port (src/dhc/cordis)                                  │
│      Context, EventEmitter, @plugin, waterfall, dispose            │
├────────────────────────────────────────────────────────────────────┤
│  L4  10 core modules (src/dhc/modules/cN_* )                       │
│      C1 GuiWebCore · C2 SessionLog · C3 PromptAssembler           │
│      C4 ToolGuard · C5 AgentRegistry · C6 TurnStepDriver           │
│      C7 LLMStreamAdapter · C8 WebhookDispatch                      │
│      C9 CapabilityPolicy · C10 ObservabilitySink                  │
├────────────────────────────────────────────────────────────────────┤
│  L3  Plugin marketplace (src/dhc/plugins)                          │
│      Manifest + SHA-256 + loader + 5 bundled plugins              │
│      rate_limiter_v1, session_exporter_v1, model_router_v1,        │
│      memory_store_v1, prompt_browser_v1                            │
├────────────────────────────────────────────────────────────────────┤
│  L2  Eval pipeline (src/dhc/eval)                                  │
│      Master prompts, paste-and-score, in-proc subprocess           │
├────────────────────────────────────────────────────────────────────┤
│  L1  Scoring engine (src/dhc/scoring)                              │
│      DHC-V = functionality * (security / 100), hard floor at 50    │
└────────────────────────────────────────────────────────────────────┘
```

## Turn/Step waterfall

The execution model is a **waterfall**: each event can be intercepted by
listeners, and waterfall events let listeners mutate the running value.

```
  turn/start
      │
      ▼
  agent/pre-step   (waterfall — listeners return mutated state)
      │
      ▼
  step/start ──► llm/stream ──► tool/call ──► step/end
                                              │
                                              ▼ (next step, up to max_steps)
                                          step/start ...
                                              │
                                              ▼ (no more steps)
                                          turn/end
```

Listeners on `tools/pre-execute` enforce capability policy (C9) before
any tool runs. Listeners on `session/event` and `tool/result` route to
C2 (SessionLog) and C10 (ObservabilitySink).

## Cordis port

A minimal Python port of [Cordis](https://github.com/cordiverse/cordis) at
`src/dhc/cordis/`. The surface is small but stable:

```python
from dhc.cordis.context import Context
from dhc.cordis.plugin import plugin

ctx = Context()
ctx.provide("tools", my_tools)
service = ctx.inject("tools")

@plugin("my_plugin")
async def apply(ctx: Context, config: dict):
    ctx.provide("gui", my_gui)
    async def dispose():
        await my_gui.stop()
    return dispose

await apply(ctx, {"port": 3080})
await ctx.dispose()  # disposes in reverse
```

Key invariants of the port:

- `Context.dispose()` is `async` and disposes in **reverse** registration
  order. Exceptions during disposal are routed to C10 telemetry, never
  silently lost.
- `EventEmitter` accepts both `def` and `async def` listeners.
- `EventEmitter.waterfall(event, value)` chains listeners so each one
  receives the previous listener's return value.
- `@plugin` wraps an `apply(ctx, config)` callable and **must return the
  disposable(s)** so the context can register them.

## Plugin marketplace

Every plugin lives at `src/dhc/plugins/<id>/` with two files:

- `manifest.json` — pydantic-validated, `extra="forbid"`, includes
  `id`, `name`, `version`, `description`, `entrypoint`, `events`,
  `config_schema`, and a 64-hex `sha256` of `service.py`.
- `service.py` — exports a `@plugin(<id>)`-decorated `apply(ctx, config)`.

The loader (`src/dhc/plugins/loader.py`) **verifies the SHA-256 with
`hmac.compare_digest`** before importing the module. A plugin whose
`service.py` has been edited without bumping the manifest SHA will
**refuse to load**.

The 5 bundled plugins in this version:

| ID | Role |
|---|---|
| `rate_limiter_v1` | Per-agent-event throttle; emits `system/throttled` |
| `session_exporter_v1` | Snapshot the C2 session log to JSONL on demand |
| `model_router_v1` | Choose backend C7 by prompt prefix or capability tag |
| `memory_store_v1` | Simple key/value store; survives one turn |
| `prompt_browser_v1` | Exposes the 10 master prompts via `/prompts` |

## Web client

A 3-tab React app served by C1:

- **Modules** — health dots for the 10 core modules + load/unload buttons
  for the 5 plugins + a paste-and-score box for in-browser eval.
- **Events** — the live WebSocket event stream. The only place
  `dangerouslySetInnerHTML` is used; every payload is sanitized via
  `renderMarkdown` or `renderToolResult` (DOMPurify + marked) before
  injection. See `apps/web/src/panels/EventsPanel.tsx`.
- **Prompts** — list of the 10 master prompts; click to view body.
  Requires `prompt_browser_v1` to be loaded.

## Scoring

`DHC-V = functionality * (security / 100)`. If `security < 50` (or
`floor_triggered`), DHC-V is `0.0`. The formula is **multiplicative,
never additive**.

Bands:

| Band | Range |
|---|---|
| `production_ready` | ≥ 80 |
| `experimental` | 50 – 79 |
| `unsafe` | < 50 |

The reference implementation self-scores `DHC-V = 100.0` (10 modules,
0 findings).
