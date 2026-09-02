# Plugin Authoring Guide

This document describes how to write a Cordis plugin for the DHC
harness. The five bundled plugins are good references:

- `rate_limiter_v1` — throttles `tool/call` events.
- `session_exporter_v1` — snapshots the C2 session log.
- `model_router_v1` — selects a backend C7 by prompt prefix.
- `memory_store_v1` — exposes a key/value store on the context.
- `prompt_browser_v1` — adds `/prompts` and `/prompts/{key}` routes.

## Layout

Every plugin lives at `src/dhc/plugins/<id>/`:

```
src/dhc/plugins/
└── <id>/
    ├── __init__.py
    ├── manifest.json
    └── service.py
```

`<id>` must match `^[a-z][a-z0-9_]*$` and have a `version` in
`manifest.json` literal-typed as one of the known versions (currently
`0.1.0`).

## `manifest.json`

```json
{
  "id": "rate_limiter_v1",
  "name": "Rate Limiter",
  "version": "0.1.0",
  "description": "Per-agent-event throttle; emits system/throttled.",
  "entrypoint": "service:rate_limiter_v1",
  "events": ["tool/call", "llm/stream"],
  "config_schema": {
    "type": "object",
    "properties": {
      "rate": { "type": "number", "minimum": 0.1 },
      "burst": { "type": "number", "minimum": 1.0 }
    },
    "required": ["rate", "burst"],
    "additionalProperties": false
  },
  "sha256": "7601a11d5c671fe6abb6bbed55b047a5f3c6d4c053eace5e93cfc8b35409d6d8"
}
```

Rules enforced by the loader (`src/dhc/plugins/_manifest.py`):

- `pydantic.BaseModel`, `extra="forbid"`, `strict=True`. Unknown
  fields are rejected.
- `sha256` must be a 64-character lowercase hex string.
- The actual SHA-256 of `service.py` is recomputed at load time and
  compared with `hmac.compare_digest`. Mismatch →
  `PluginIntegrityError`.

## `service.py`

The minimal skeleton:

```python
from __future__ import annotations
from dhc.cordis.context import Context
from dhc.cordis.plugin import plugin


@plugin("my_plugin_v1")
async def apply(ctx: Context, config: dict) -> object:
    # ... wire services, register listeners, allocate resources ...

    async def dispose() -> None:
        # ... run cleanup in reverse order ...
        pass

    return dispose
```

Rules enforced by `dhc.cordis.plugin.plugin`:

- `apply` may be `def` or `async def`.
- The return value is treated as a **disposable**: `None` is fine,
  a single callable is fine, an iterable of callables is fine.
- Disposables are stored on the context's disposable stack and run
  in reverse when `ctx.dispose()` is called.

## Subscribing to events

```python
async def on_tool_call(payload):
    ...

ctx.events.on("tool/call", on_tool_call)

# Detach later
ctx.events.off("tool/call", on_tool_call)
```

For waterfall events, **return the mutated value**:

```python
async def pre_step(state, agent_id):
    state["notes"] = state.get("notes", []) + ["pre-step run"]
    return state

ctx.events.on("agent/pre-step", pre_step)
```

## Adding HTTP routes

The C1 bridge exposes a few hook points for plugins:

```python
async def apply(ctx, config):
    core = ctx.inject("gui_core")  # the aiohttp Application
    repo_root = ctx.inject("repo_root")

    async def my_handler(request):
        return web.json_response({"ok": True})

    core.add_get("/my-route", my_handler)

    async def dispose():
        # No remove API in aiohttp; the app dies with the harness.
        pass

    return dispose
```

A cleaner pattern (used by `prompt_browser_v1`) is to register
unload-time cleanup through the context.

## Computing the SHA

When you change `service.py`, run:

```bash
python -m dhc.plugins._sha my_plugin_v1
```

Copy the output into the `"sha256"` field of `manifest.json`. The
next `POST /plugins/my_plugin_v1` will fail with
`PluginIntegrityError` until you do.

The current locked SHAs for the 5 bundled plugins are in
[`SHA-PINNING.md`](SHA-PINNING.md).

## Loading and unloading

Load:

```bash
curl -X POST http://127.0.0.1:<port>/plugins/my_plugin_v1 \
     -H "Authorization: Bearer <token>" \
     -H "Content-Type: application/json" \
     -d '{"config": {"rate": 5.0}}'
```

Unload:

```bash
curl -X DELETE http://127.0.0.1:<port>/plugins/my_plugin_v1 \
     -H "Authorization: Bearer <token>"
```

Both routes are registered in C1 (`src/dhc/modules/c1_gui_web_core/service.py`)
and the smoke tests in `tests/plugins/test_c1_plugin_routes.py`
exercise them.

## Tests

Every bundled plugin has a test file in `tests/plugins/`:

- `test_manifest_and_loader.py` — generic manifest/loader contract.
- `test_bundled_plugins.py` — load + apply + dispose each plugin.
- `test_isolation.py` — two plugins loaded into the same context do
  not see each other's state.
- `test_c1_plugin_routes.py` — end-to-end against the C1 aiohttp
  app.

When you add a new plugin, add at least one test that loads it,
exercises one event, and unloads it.
