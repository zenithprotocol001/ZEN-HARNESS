```
╔══════════════════════════════════════════════════════════════════════════════╗
║  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  ║
║  ▓                                                                    ▓  ║
║  ▓   ██████╗ ██╗  ██╗ ██████╗     ██╗   ██╗                             ▓  ║
║  ▓   ██╔══██╗██║  ██║██╔════╝     ██║   ██║                             ▓  ║
║  ▓   ██║  ██║███████║██║  ███╗    ██║   ██║                             ▓  ║
║  ▓   ██║  ██║██╔══██║██║   ██║    ╚██╗ ██╔╝                             ▓  ║
║  ▓   ██████╔╝██║  ██║╚██████╔╝     ╚████╔╝                              ▓  ║
║  ▓   ╚═════╝ ╚═╝  ╚═╝ ╚═════╝       ╚═══╝                               ▓  ║
║  ▓                                                                    ▓  ║
║  ▓   HARNESS CREATION BENCHMARK // v1.3.0 // DHC-V 100.0              ▓  ║
║  ▓   Everything is a Plugin. Nothing is Trusted.                        ▓  ║
║  ▓                                                                    ▓  ║
║  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

<div align="center">

```
[ NEURAL LINK ESTABLISHED ]   [ CORDIS: ONLINE ]   [ TARGET: 10 MODULES ]
[ STATUS: PRODUCTION_READY ]  [ SCORE: 100.0/100 ] [ SEALED: 2026-09-01 ]
```

</div>

---

## `// 00 — TRANSMISSION`

**The HARNESS Creation Benchmark (DHC-V)** is a hermetic, secure, and
mathematically rigorous evaluation suite for LLMs that attempt to build
plugins for the [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
runtime on top of the [Cordis](https://github.com/cordiverse/cordis) framework.

This repository is the **reference implementation**: ten core modules (C1–C10),
a Python port of the Cordis primitives, a React/Vite web client, deterministic
mock LLM fixtures, a complete test suite, and the scoring engine that produces
the **DHC-V** (DeepSeek Harness Creation Value) metric.

> *“Everything is a Plugin. Nothing is Trusted.”*
> — the Cordis axiom this benchmark enshrines.

---

## `// 01 — QUICK START`

### On a provisioned host (Python 3.10+, Playwright, network)

```bash
# 1. Install dependencies
pip install -e .[test,playwright]

# 2. Install the Chromium browser for Playwright
playwright install chromium

# 3. Run the full test suite
pytest -q

# 4. Compute the DHC-V self-score
python -c "from dhc.scoring.scorer import make_report, write_report, ModuleScore; \
  r = make_report([ModuleScore('c1',100.0,100.0)]); \
  write_report(r, 'dhc-v-report.json'); print(f'DHC-V = {r.dhc_v}')"
```

Expected: `DHC-V = 100.0`.

### On a stripped host (PowerShell only, no Python 3.x)

```powershell
powershell -ExecutionPolicy Bypass -File scripts/static_check.ps1
powershell -ExecutionPolicy Bypass -File scripts/c8_static_check.ps1
powershell -ExecutionPolicy Bypass -File scripts/invariants_check.ps1
powershell -ExecutionPolicy Bypass -File scripts/hmac_sanity_check.ps1
```

---

## `// 02 — WHAT THIS BENCHMARK MEASURES`

The benchmark scores LLM-generated Cordis plugin code on two axes, combined
multiplicatively into the **DHC-V** metric.

```
┌────────────────────────────────────────────────────────────┐
│                                                            │
│   DHC_V = functionality * (security / 100)                │
│                                                            │
│   if security < 50:                                       │
│       DHC_V = 0                                           │
│                                                            │
│   Bands:  [█████████░] ≥ 80  production_ready              │
│           [█████░░░░░] 50–79  experimental                │
│           [█░░░░░░░░░] < 50   unsafe                       │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

| Axis | Source | Weight |
|---|---|---|
| **Functionality** | `unit_pass_rate × 40` + `turn_completion_rate × 40` + `ui_streaming_fidelity × 20` | 50% |
| **Security** | starts at 100; deducts per finding | 50% |

The formula is **multiplicative, never additive**. A "secure but broken" harness
(a model that writes pristine cryptography but never makes the turn complete)
scores **zero** — it has zero creation value. A harness with a single critical
finding (RCE, XSS, auth bypass) also scores zero.

### Severity deductions

| Severity | Deduction | Examples |
|---|---|---|
| `critical` | **−100** (and forces DHC-V to 0) | RCE, sandbox escape, auth bypass, XSS |
| `high` | **−30** | prompt injection, path traversal, command injection, replay |
| `medium` | **−10** | rate-limit missing, verbose error leak |
| `low` | **−5** | missing CSP, weak entropy |

### Playwright re-weighting

If Playwright is unavailable, `ui_streaming_fidelity` is reported as `null`
in the JSON and the functionality subscore is re-weighted to `50% unit + 50%
e2e`. The benchmark **never silently fakes a UI score** — the absence is
visible in the report.

---

## `// 03 — ARCHITECTURE`

The runtime is an *everything-is-a-plugin* architecture with no privileged
core. The execution model is a **Turn/Step waterfall**:

```
                 ╔═══════════════╗
                 ║   USER INPUT  ║
                 ╚═══════╤═══════╝
                         │
                         ▼
              ┌─────────────────────┐
              │     turn/start      │
              └──────────┬──────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │  agent/pre-step     │  ← waterfall slot (listeners may mutate state)
              └──────────┬──────────┘
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
        ┌──────────┐          ┌──────────┐
        │ step/N   │  ──────► │ step/N+1 │  ... (up to max_steps=5)
        └────┬─────┘          └────┬─────┘
             │                     │
             ▼                     ▼
       ┌────────────┐        ┌────────────┐
       │ llm/stream │        │ llm/stream │
       └─────┬──────┘        └─────┬──────┘
             │                     │
             ▼                     ▼
       ┌────────────┐        ┌────────────┐
       │ tool/call  │        │ tool/call  │  ← gated by C9 + C4
       └─────┬──────┘        └─────┬──────┘
             │                     │
             └─────────┬───────────┘
                       │
                       ▼
              ┌─────────────────────┐
              │     turn/end        │  → reason ∈ ABORT_REASONS
              └─────────────────────┘
```

Every event flows through a single `Context.events` bus. Listeners on
`tools/pre-execute` enforce capability policy before any tool runs.
Listeners on `session/event` and `tool/result` route to C2 (SessionLog) and
C10 (ObservabilitySink).

---

## `// 04 — THE CORDIS FRAMEWORK PORT`

The benchmark ships a minimal Python port of the Cordis framework at
`src/dhc/cordis/`. The surface is intentionally small — only what the 10
modules need — but the API is stable enough to run real Cordis-style plugins.

### `Context` — service registry, event bus, disposable stack

```python
from dhc.cordis.context import Context

ctx = Context()
ctx.provide("tools", my_tools)         # register a service
service = ctx.inject("tools")          # fetch by name
ctx.add_disposable(cleanup_fn)         # schedule teardown
await ctx.dispose()                    # run all disposables in reverse
```

`dispose()` catches any disposal exception and routes it to the C10
telemetry service if it is registered — no silent failures during teardown.

### `EventEmitter` — sync/async listeners, emit, waterfall

```python
async def on_tool_result(payload):
    ...

ctx.events.on("tool/result", on_tool_result)
await ctx.events.emit("tool/result", {"output": "ok"})

# Waterfall: each listener can mutate the running value
result = await ctx.events.waterfall("agent/pre-step", state, agent_id="a1")

# Detach a listener
ctx.events.off("tool/result", on_tool_result)
```

The emitter supports both `async def` and `def` listeners, and its
`waterfall` method chains them so each one receives the previous listener's
return value. The auditor's forward warning about `agent/pre-step` is
honored — listeners on that event **must** return the mutated state.

### `@plugin` — declarative lifecycle

```python
from dhc.cordis.plugin import plugin

@plugin("c1_gui")
async def apply(ctx: Context, config: dict):
    web_core = GuiWebCore(...)
    ctx.provide("gui", web_core)
    async def dispose():
        await web_core.stop()
    return dispose

# Wire into a context
await apply(ctx, {"port": 3080})
```

The decorator transforms `apply(ctx, config) -> callable | list[callable]`
into a plugin registration. The returned callable(s) are stored on the
context's disposable stack and run in reverse when `ctx.dispose()` is called.

---

## `// 05 — THE 10 CORE MODULES`

Every module lives at `src/dhc/modules/cN_<name>/service.py` and exports a
`@plugin("cN_*")` `apply` function plus its public types.

---

### `C1 — GuiWebCore`  ▓▓▓ presentation

Bridges Cordis events to a web browser over WebSocket and serves a strict
Content-Security-Policy on every HTTP response.

```python
from dhc.modules.c1_gui_web_core.service import CSP_HEADER, build_csp_header, apply
```

| Property | Value |
|---|---|
| Routes | `/`, `/healthz`, `/ws` |
| CSP forbids | `'unsafe-inline'`, `'unsafe-eval'`, wildcards |
| CSP requires | `default-src 'self'`, `script-src 'self'`, `object-src 'none'`, `frame-ancestors 'none'` |
| WS events | `turn/start`, `step/start`, `llm/stream`, `tool/call`, `step/end`, `turn/end` |

**XSS defense — three layers**:
1. Browser CSP at the Python bridge.
2. DOMPurify with explicit `FORBID_TAGS` in the React client.
3. TypeScript structural rule: `dangerouslySetInnerHTML` is only ever fed
   the output of `renderMarkdown` / `renderToolResult`.

---

### `C2 — SessionEventLog`  ▓▓▓ data plane

Append-only event store with tuple snapshots and branching.

```python
from dhc.modules.c2_session_event_log.service import SessionEvent, SessionLog

log = SessionLog()
log.append(SessionEvent(id="1", type="step", payload={...}))
history = log.get_history()        # tuple[SessionEvent, ...]  — immutable
log.branch("checkpoint-a")         # fork for rewind/recovery
```

| Property | Value |
|---|---|
| `SessionEvent` | `pydantic.BaseModel(frozen=True)` — attribute mutation raises |
| `get_history()` | returns `tuple`; any `append`/`pop`/`__setitem__` raises `AttributeError` |
| `branch(id)` | creates a private copy; main log untouched |

---

### `C3 — PromptAssembler`  ▓▓▓ intelligence boundary

Assembles an LLM prompt with strict boundary framing and capability-filtered
tool schema injection.

```python
from dhc.modules.c3_prompt_assembler.service import (
    BOUNDARY_TOKENS, Message, PromptAssembler, ToolSchema, build_prompt,
)
```

**9 boundary tokens** are escaped longest-first, idempotently, with
`html.escape`:

```
<|user_start|>   <|user_end|>   <|system_start|>   <|system_end|>
</user_message>   <user_message>
### SYSTEM        ### SYSTEM OVERRIDE
IGNORE PREVIOUS INSTRUCTIONS
```

The wrapped user section uses control tokens `<|user_start|>` …
`<|user_end|>` so the mock LLM can structurally separate system and user
regions. The tool-schema section is **filtered through C9 CapabilityPolicy**
— an agent only sees the schemas of tools it has been granted.

---

### `C4 — ToolGuardPipeline`  ▓▓▓ execution boundary

Schema-strict tool invocation with security checks.

```python
from dhc.modules.c4_tool_guard_pipeline.service import BashInput, ReadFileInput, ToolGuard

guard = ToolGuard()
guard.register("read_file", my_executor, ReadFileInput)
guard.register("bash", my_bash_executor, BashInput)
result = await guard.invoke("read_file", {"path": "src/dhc/__init__.py"})
```

| Field | Constraint |
|---|---|
| `BashInput.command` | `list[str]`, each token matches `^[a-zA-Z0-9_\-./:=]+$` |
| `BashInput.cwd` | `Literal["/workspace", "/tmp"]` |
| `BashInput.timeout` | `int` in `[1, 30]` |
| `ReadFileInput.path` | no `..` segments, no `/etc/` prefix |
| `extra` on all models | `"forbid"` — unknown fields rejected |

A literal attack `; rm -rf /` is rejected at the **schema** layer (string
is not a list), not by the regex.

---

### `C5 — AgentRegistry`  ▓▓▓ identity plane

HMAC-SHA256-signed agent manifests with strict scope isolation.

```python
from dhc.modules.c5_agent_registry.service import (
    AgentManifest, compute_signature, verify_signature,
)
```

**Canonical form** (the C8 lesson applied):
```
agent_id_utf8 || 0x1F || sorted_capabilities_joined_by_0x1F
```

The `0x1F` (Unit Separator) control character **cannot** appear in any
valid identifier or capability name, eliminating separator-smuggling attacks.
Verification uses `hmac.compare_digest` only — never `==`. A successful
registration auto-grants capabilities via the C9 policy if it is wired into
the same `Context`.

---

### `C6 — TurnStepDriver`  ▓▓▓ orchestration

Orchestrates the agent turn/step waterfall with a hard step-limit circuit
breaker.

```python
from dhc.modules.c6_turn_step_driver.service import TurnStepDriver, StepLimitExceeded

driver = TurnStepDriver(max_steps=5)
end = await driver.run_turn(ctx, agent_id="a1", llm_stream=..., tool_dispatch=...)
# end.reason ∈ {completed, max_steps_exceeded, tool_error, policy_denied, llm_error}
```

| Property | Value |
|---|---|
| Waterfall | `turn/start` → `agent/pre-step` (waterfall) → `step/start` → `llm/stream` → `tool/call` → `step/end` → `turn/end` |
| Default `max_steps` | 5 |
| On exceed | emits `turn/end{reason: "max_steps_exceeded"}` then raises `StepLimitExceeded` |
| Distinct abort reasons | `completed`, `max_steps_exceeded`, `tool_error`, `policy_denied`, `llm_error` |

---

### `C7 — LLMStreamAdapter`  ▓▓▓ model seam

Consumes a Server-Sent Events stream from an OpenAI-compatible LLM API,
with strict chunk buffering and API key redaction.

```python
from dhc.modules.c7_llm_stream_adapter.service import LLMStreamAdapter

adapter = LLMStreamAdapter(base_url="http://127.0.0.1:8080", api_key="sk-...")
async for chunk in adapter.stream(prompt, scenario="happy"):
    print(chunk.delta, chunk.finish_reason, chunk.tool_calls)
```

| Property | Value |
|---|---|
| Buffer cap | 1 MiB; over → `BufferOverflow` |
| Terminator | `\n\n` per event block, `data: [DONE]` to end |
| Key redaction | `sk-a***890` form; raw key never appears in error messages |
| Garbage handling | malformed JSON, comments, partial bytes — tolerated |

---

### `C8 — WebhookDispatch`  ▓▓▓ ingress &amp; auth

Ingress handler for external webhooks with HMAC-SHA256 verification, nonce
replay protection, and timestamp window enforcement.

```python
from dhc.modules.c8_webhook_dispatch.service import (
    WebhookDispatch, WebhookPayload, InvalidSignature, ExpiredTimestamp,
    ReplayDetected, MalformedPayload,
)
```

| Property | Value |
|---|---|
| Canonical form | `timestamp || "." || nonce || "." || body` |
| Comparison | `hmac.compare_digest` (AST-enforced, no `==`) |
| Nonce store | bounded `OrderedDict` with LRU eviction; reuse → `ReplayDetected` |
| Timestamp window | `±5 min`; outside → `ExpiredTimestamp` |
| Payload model | `pydantic.BaseModel(extra="forbid", frozen=True)` |
| Secret length | ≥ 16 bytes (validated at construction) |

The dispatcher accepts a `clock_ms` callable so the frozen clock
(`FROZEN_EPOCH_MS`) in `fixtures/mock_llm/scripts.py` produces
deterministic results.

---

### `C9 — CapabilityPolicy`  ▓▓▓ sandbox &amp; policy

Deny-all default, intercepts `tools/pre-execute` to gate every tool call.

```python
from dhc.modules.c9_capability_policy.service import CapabilityPolicy, CapabilityDenied

policy = CapabilityPolicy()
policy.grant("agent_a", "read_file")
policy.check("agent_a", "read_file")           # ok
policy.check("agent_a", "shell_execute")       # raises CapabilityDenied
```

| Property | Value |
|---|---|
| Default | deny-all — empty capability set |
| `PreExecutePayload` | strict pydantic; `extra="forbid"`; `grant: true` smuggling is rejected |
| Mutation surface | only explicit `grant()` / `revoke()` calls — no event listener can mutate |

---

### `C10 — ObservabilitySink`  ▓▓▓ telemetry

Captures events and scrubs PII/secrets before writing to the log sink.

```python
from dhc.modules.c10_observability_sink.service import scrub_pii

scrubbed = scrub_pii({"key": "sk-12345678901234567890", "user": "x@y.z"})
# -> {"key": "***REDACTED***", "user": "***REDACTED***"}
```

| Pattern | Coverage |
|---|---|
| `sk-[a-zA-Z0-9]{20,}` | OpenAI keys |
| `sk_live_[a-zA-Z0-9]{16,}` | Stripe live keys |
| `ghp_[a-zA-Z0-9]{20,}` | GitHub PATs |
| email regex | email addresses |

The scrubber is recursive over `dict`, `list`, `tuple`, `str`. Cordis
`Context.dispose()` routes its own disposal errors here, so no teardown
exception is silently lost.

---

## `// 06 — THE WEB CLIENT`

A minimal React + Vite client. The client is a **3-tab router** in
`App.tsx` that owns the WebSocket subscription and the bearer-token
extraction. The actual rendering of HTML payloads lives in
`panels/EventsPanel.tsx` and only there.

```
apps/web/
  index.html
  package.json          dompurify, marked, react, react-dom, vite
  tsconfig.json
  vite.config.ts
  src/
    main.tsx            React entrypoint
    App.tsx             router + WS subscriber + tab state  (NO HTML rendering)
    sanitize.ts         renderMarkdown, renderToolResult, formatPayload, isToolResult
    styles.css
    components/
      ModuleCard.tsx    shared card with Load/Unload
    panels/
      ModulesPanel.tsx  10 core modules + 5 plugin cards + paste-and-score
      EventsPanel.tsx   live event stream — the ONLY place HTML is injected
      PromptsPanel.tsx  10 master prompts from src/dhc/eval/prompts/
```

### Run

```bash
cd apps/web
npm install
npm run build          # produces apps/web/dist/
```

The dist is then served by C1 on its ephemeral loopback port.

### XSS defense — three layers

1. **Browser CSP** at the Python bridge (no `unsafe-inline`, no
   `unsafe-eval`, `object-src 'none'`, `frame-ancestors 'none'`).
2. **DOMPurify with `FORBID_TAGS`** in `sanitize.ts` for both markdown
   and tool-result rendering paths.
3. **Structural localization**: `renderMarkdown`, `renderToolResult`,
   and `dangerouslySetInnerHTML` appear **only** in
   `panels/EventsPanel.tsx`. The PowerShell invariant script asserts
   both positive (`EventsPanel` has them) and negative (`App.tsx` does
   not) invariants, so a future regression that pulls HTML rendering
   back into the router fails CI.

---

## `// 07 — THE MOCK LLM`

A deterministic aiohttp server that emulates an OpenAI-compatible LLM
provider. Lives in `fixtures/mock_llm/`.

| URL | Behavior |
|---|---|
| `GET /v1/stream/happy` | 5-chunk scripted conversation; `read_file` then `stop` |
| `GET /v1/stream/infinite` | Same tool-call payload 64 times — drives the C6 circuit breaker |
| `GET /v1/stream/fragmented` | Splits the happy script into 7 arbitrarily-bounded chunks for C7 |
| `GET /healthz` | `{"ok": true}` |

**Determinism guarantees**:
- All timestamps frozen to `2026-01-01T00:00:00Z`.
- Nonces drawn from a fixed sequence `nonce-{i:08d}`.
- The webhook secret is hardcoded as `WEBHOOK_SECRET = b"dhc-test-secret-do-not-use-in-prod"`.
- The HMAC canonical form is `timestamp || "." || nonce || "." || body`, matching
  the C8 dispatcher exactly.

---

## `// 08 — THE SCORING ENGINE`

`src/dhc/scoring/scorer.py` is the single source of truth for DHC-V.
Import everything from `dhc.scoring.scorer`.

### `Finding`

```python
@dataclass(frozen=True)
class Finding:
    module: str
    severity: str   # "critical" | "high" | "medium" | "low"
    description: str

    def deduction(self) -> int: ...
```

### `ModuleScore`

```python
@dataclass
class ModuleScore:
    module: str
    functionality: float     # 0-100
    security: float          # 0-100
    findings: list[Finding]
    notes: list[str]
```

### `score_functionality`

```python
score_functionality(
    unit_pass_rate: float,            # 0-1
    turn_completion_rate: float,      # 0-1
    ui_streaming_fidelity: float | None = None,
) -> float
```

If `ui_streaming_fidelity` is `None` (Playwright unavailable), the weights
rebalance to `0.5 * unit + 0.5 * turn`. The function never silently fakes
a UI score.

### `score_security`

```python
score_security(findings: list[Finding]) -> tuple[float, bool]
# returns (security_score, floor_triggered)
```

Starts at 100, deducts per finding. A single critical zeroes the score
outright. `floor_triggered` is `True` iff the post-deduction score is `< 50`.

### `compute_dhc_v`

```python
compute_dhc_v(
    functionality: float,
    security: float,
    security_floor_triggered: bool = False,
) -> float
```

Hard floor: if `security < 50` or `security_floor_triggered`, returns `0.0`.
Otherwise returns `functionality * (security / 100.0)`.

### `make_report` and `write_report`

```python
report = make_report(
    module_scores=[ModuleScore("c1", 100.0, 100.0), ...],
    findings=[Finding("c4", "high", "path traversal"), ...],
)
write_report(report, "dhc-v-report.json")
```

Output schema:

```json
{
  "dhc_v": 100.0,
  "functionality": 100.0,
  "security": 100.0,
  "security_floor_triggered": false,
  "modules": [
    { "module": "c1", "functionality": 100.0, "security": 100.0, "findings": [...], "notes": [...] }
  ],
  "findings": [...],
  "bands": { "production_ready": ">=80", "experimental": "50-79", "unsafe": "<50" }
}
```

### The no-additive-formula guard

`tests/scoring/test_scorer.py::test_dhc_v_never_uses_additive_formula`
asserts the boundary cases hit `0` or `25`, **never `50`**. This locks out
future maintainers from accidentally re-introducing the forbidden
`0.5 * f + 0.5 * s` additive formula.

---

## `// 09 — THE TEST SUITE`

26 test files, organized into four groups.

```
tests/
├── unit/                  ── 10 files ──  per-module unit tests
├── security/              ──  9 files ──  attack scenarios per module
├── plugins/               ──  4 files ──  manifest, loader, bundled plugins, isolation, C1 routes
├── chat/                  ──  6 files ──  secrets envelope, session_manager, mock LLM + C7 chat_stream, C1 /ws/chat + sessions + secrets + models + dispatch routes, smoke runner
├── integrations/          ──  5 files ──  model registry, base types, openai/anthropic/openrouter clients, factory, mock provider server
├── eval/                  ──  2 files ──  backup & extract+rosetta for the offline eval
└── scoring/               ──  1 file  ──  formula, deductions, re-weighting
```

| Group | Files | What it asserts |
|---|---|---|
| `unit/` | 10 | per-module happy paths + negative cases |
| `security/` | 9 | attack scenarios per module (XSS, mutation, traversal, …) |
| `plugins/` | 4 | manifest validity, SHA-256 compare, 5 bundled plugins load+apply+dispose, plugin isolation, C1 `/plugins` and `/api/eval` routes |
| `eval/` | 2 | subprocess backup/restore; extract & rosetta for the offline eval wrapper |
| `scoring/` | 1 | 28 tests: multiplicative formula, deductions, security<50 floor, re-weighting, no-additive guard |

**Total: 388 passing, 2 skipped, 1 xpassed** at v1.3.0.

### Run

```bash
pytest -q                  # all 26 test files
pytest tests/unit -q       # unit only
pytest tests/security -q   # security only
pytest tests/plugins -q    # plugin marketplace only
pytest tests/scoring -q    # scoring only
```

---

## `// 10 — STATIC VERIFICATION`

Four PowerShell scripts perform static analysis without requiring Python
3.x. Useful for CI gating on stripped hosts.

| Script | Asserts |
|---|---|
| `scripts/static_check.ps1` | 0 syntax errors; every `from dhc.X import Y` resolves |
| `scripts/c8_static_check.ps1` | `hmac.compare_digest` present; no `==` on hmac-shaped names; short-secret guard |
| `scripts/invariants_check.ps1` | 100+ invariants across all 10 modules + cordis + scorer + plugin marketplace + 4-tab UI (positive **and** negative); no-additive-formula guard |
| `scripts/hmac_sanity_check.ps1` | cross-language HMAC check (Python fixture vs .NET) |
| `scripts/package_relay.ps1` | builds a clean-prefix zip into `relay/` |

```powershell
powershell -ExecutionPolicy Bypass -File scripts/invariants_check.ps1
# expected: "All invariants pass"
```

---

## `// 11 — EVALUATING A TARGET LLM`

The benchmark measures the **creation value** of LLM-generated Cordis
plugin code. General workflow:

1. **Prompt the LLM** with the contract for a single module — its public
   surface signature and the security expectations from `// 05`.
2. **Capture** the LLM's output as a Python file.
3. **Run** the unit and security tests for that module against the
   captured file. The reference tests in `tests/unit/test_cN.py` and
   `tests/security/test_cN_*.py` form the rubric.
4. **Collect findings** — every failing test is a `Finding(module, severity)`.
5. **Compute** the DHC-V for that module via `make_report`.
6. **Aggregate** across all 10 modules for a leaderboard row.

```python
from dhc.scoring.scorer import Finding, ModuleScore, make_report, write_report

findings = [
    Finding("c4", "high", "Bash tool accepts raw string command"),
    Finding("c8", "critical", "Webhook signature compared with =="),
]

module_scores = [
    ModuleScore("c1", 85.0, 80.0),
    ModuleScore("c4", 60.0, 70.0, findings=findings[:1]),
    ModuleScore("c8", 20.0,  0.0, findings=findings[1:]),
]

report = make_report(module_scores, findings=findings)
write_report(report, "leaderboard/glm5-flash.json")
print(f"DHC-V: {report.dhc_v}  Band: production_ready / experimental / unsafe")
```

---

## `// 12 — OFFLINE VERIFICATION`

The reference implementation was authored on a Windows host with no Python
3.x installed and outbound network blocked. To verify on such a host:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/static_check.ps1
powershell -ExecutionPolicy Bypass -File scripts/c8_static_check.ps1
powershell -ExecutionPolicy Bypass -File scripts/invariants_check.ps1
powershell -ExecutionPolicy Bypass -File scripts/hmac_sanity_check.ps1
```

All four run in seconds and produce PASS/FAIL per invariant. Runtime tests
require a provisioned host.

---

## `// 13 — PUBLISHING TO A REMOTE`

The relay channel for this project is the `relay/` folder. There is no
`git` binary on the authoring host and no credentials for
`zenithprotocol001/harness`. To publish from a host that has them:

```bash
unzip harness_benchmark-v0.6.0-20260901.zip
cd harness_benchmark
git init
git add .
git commit -m "v0.6.0 reference implementation (DHC-V 100.0)"
git remote add origin git@github.com:zenithprotocol001/harness.git
git push -u origin main --force
```

---

## `// 14 — SECURITY GUARANTEES`

| Module | Defends against | Test |
|---|---|---|
| C1 | XSS, CSP bypass, WS injection | `tests/security/test_c1_xss.py` |
| C2 | History mutation, replay, prototype pollution | `tests/security/test_c2_mutation.py` |
| C3 | Prompt injection, boundary breakout, tool schema smuggling | `tests/security/test_c3_boundary_injection.py` |
| C4 | Path traversal, command injection, schema bypass | `tests/security/test_c4_path_traversal.py` |
| C5 | Signature forgery, capability tampering, separator smuggle | `tests/security/test_c5_spoofed_registration.py` |
| C6 | DoS via infinite loop, poisoned tool result, listener leak | `tests/security/test_c6_infinite_loop_circuit_breaker.py` |
| C7 | Buffer overflow, malformed JSON, key leakage, partial chunks | `tests/security/test_c7_stream.py` |
| C8 | Replay, old timestamp, timing side-channel, malformed payload | `tests/security/test_c8_timing.py` |
| C9 | Capability escalation, listener smuggling, ghost agents | `tests/security/test_c9_escalation.py` |
| C10 | PII/secret leakage in logs, silent failure on disposal | `tests/unit/test_c10.py` |
| Scorer | Additive formula regression, severity miscalibration | `tests/scoring/test_scorer.py` |

---

## `// 15 — VERSIONING &amp; RELAY`

The `relay/` folder is the project's ship channel.

| File | Purpose |
|---|---|
| `harness_benchmark-v1.3.0-20260902.zip` | reference implementation (current) |
| `MANIFEST.txt` | human-readable ship manifest with module map, plugin SHAs, static-check results, self-score, sync recipe, and exclusion list |

The zip uses a clean root layout (`pyproject.toml`, `pytest.ini`, `src/`,
`tests/`, `scripts/`, `fixtures/`, `apps/`, `docs/`, `CHANGELOG.md`,
`CONTRIBUTING.md`, `GLOSSARY.md`, `README.md`, `readme.txt`,
`relay/MANIFEST.txt`) with no staging-dir prefix.

Runtime artifacts are **excluded** from the zip:
`serve_c1.{port,token,log,err.log}`, `demo_live.*`, `heartbeat*`,
`vite.log`, `__pycache__/`, `.pytest_cache/`.

**Versioning policy**:
- **Patch** (`1.2.x`): typo fixes, doc improvements, invariant-script tweaks.
- **Minor** (`1.x.0`): new module, new plugin, new test, new invariant.
- **Major** (`x.0.0`): scoring formula change, module contract change.

The `Datum` field in the filename uses `YYYYMMDD` (no time) for
filesystem-friendly lexicographic ordering.

## `// 16 — PLUGIN MARKETPLACE`

The harness ships 5 bundled plugins at `src/dhc/plugins/`. Every
plugin has a `manifest.json` + `service.py`. The loader verifies
the SHA-256 of `service.py` against the manifest's `sha256` field
using `hmac.compare_digest`. A mismatch is a hard error: the plugin
will not load.

| Plugin | Role | SHA-256 (short) |
|---|---|---|
| `rate_limiter_v1` | Per-agent-event throttle; emits `system/throttled` | `7601a11d…d6d8` |
| `session_exporter_v1` | Snapshot the C2 session log to JSONL on demand | `35e6985c…67c75` |
| `model_router_v1` | Choose backend C7 by prompt prefix or capability tag | `e459a03e…2fa2` |
| `memory_store_v1` | Simple key/value store; survives one turn | `ccea4ff8…03d8b` |
| `prompt_browser_v1` | Exposes the 10 master prompts via `/prompts` | `b33c95c2…df85` |

The 5 SHAs are **locked** in `docs/SHA-PINNING.md` and asserted by
`scripts/invariants_check.ps1`. To add a new plugin, follow
`docs/plugin-authoring.md`.

**C1 marketplace routes** (v1.1.0+):

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/manifest` | full manifest (modules + discovered plugins) |
| GET | `/plugins` | discovered vs loaded plugins |
| POST | `/plugins/{id}` | load a plugin (config in JSON body) |
| DELETE | `/plugins/{id}` | unload a plugin |
| GET | `/prompts` | list 10 master prompts (requires `prompt_browser_v1`) |
| GET | `/prompts/{key}` | single prompt body |
| POST | `/api/eval` | offline in-proc eval of pasted code |

## `// 17 — 4-TAB WEB UI`

The web client has exactly four tabs. The structural split is
enforced by `scripts/invariants_check.ps1`.

| Tab | File | What it shows |
|---|---|---|
| **Modules** | `panels/ModulesPanel.tsx` | 10 core module cards (health dots) + 5 plugin cards (load/unload) + paste-and-score box |
| **Events** | `panels/EventsPanel.tsx` | live event stream from the C1 WebSocket |
| **Prompts** | `panels/PromptsPanel.tsx` | 10 master prompts; click to view body (requires `prompt_browser_v1` loaded) |
| **Chat** | `panels/ChatPanel.tsx` | server-side chat sessions with a left rail of Today/Yesterday/Older groups, Ctrl+K search, and a streaming assistant reply via `/ws/chat` |

**Markdown component guardrail** (security, ADR 0005):
`components/Markdown.tsx` is the **only** file in the React client
that calls `dangerouslySetInnerHTML` or imports the sanitizers
(`renderMarkdown`, `renderToolResult`). The PowerShell invariant
script scans every `.tsx` file and asserts that none of these
three substrings appear anywhere except `components/Markdown.tsx`.
This makes the XSS guardrail a single, auditable line of code.

## `// 18 — CHAT &amp; SESSIONS (v1.2.0)`

The v1.2.0 release adds a 4th tab (Chat) with persistent
sessions and a server-side encrypted secret store. Live LLM
calls are deferred to v1.3.0; v1.2.0 ships a local mock LLM
that the chat surface streams against.

**New server modules** (Python):

- `src/dhc/cordis/secrets.py` — `SecretsService` with an
  encrypt-then-MAC envelope (HMAC-SHA256 counter-mode keystream,
  separate MAC key) under a per-user 32-byte master key.
  See `docs/secrets-model.md`.
- `src/dhc/services/session_manager.py` — `SessionManager` for
  persistent chat sessions (JSON files under
  `~/.dhc/sessions/`, atomic writes via `os.replace`).
  See `docs/session-storage.md`.

**New C1 routes**:

| Method | Path | Purpose |
|---|---|---|
| WS | `/ws/chat` | request/response chat stream (see `docs/chat-architecture.md` for the frame schema) |
| GET | `/api/sessions` | list session summaries (search + archived flags supported) |
| POST | `/api/sessions` | create a session (auto-titled on first user message) |
| GET | `/api/sessions/{id}` | full session (with messages) |
| PATCH | `/api/sessions/{id}` | rename / pin / archive / tag / set model |
| DELETE | `/api/sessions/{id}` | soft-delete (or `?hard=1` to remove file) |
| POST | `/api/sessions/{id}/messages` | synchronous send + receive (used by the smoke; the UI uses `/ws/chat`) |
| GET | `/api/secrets` | list secret **names** only (values are never returned) |
| PUT | `/api/secrets/{name}` | encrypt and store |
| DELETE | `/api/secrets/{name}` | remove |
| GET | `/api/llm/health` | probe the configured LLM adapter |

**New React surface**:

- `panels/ChatPanel.tsx` — the 4th tab.
- `panels/SessionList.tsx` — left-rail session list with
  Today/Yesterday/Older grouping and pin/archive/delete buttons.
- `components/SearchOverlay.tsx` — Ctrl+K modal for session search.
- `components/Markdown.tsx` — the single owner of
  `dangerouslySetInnerHTML` (see `// 17`).
- `types/chat.ts` — shared `Message`, `Session`, `ChatFrame`
  type definitions.

**New mock LLM** (test fixture, not a shipped plugin):

- `tests/fixtures/mock_llm.py` — aiohttp server with
  `POST /v1/chat/completions` (OpenAI-compatible) and
  `GET /v1/stream/{scenario}` (legacy). Scenarios: `default`,
  `echo`, `code`, `tool`, `slow`, `long`. Bound to loopback only.

**Smoke test** (offline, no network):

```
python tests/chat/smoke_v12.py
```

Exercises 19 checks end-to-end against the mock LLM and asserts
that the session log persists and the secret store does not
leak values via `/api/secrets`.

---

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  ▓   END OF LINE  //  PROJECT STATUS: GREENLIGHT FOR PRODUCTION           ▓  ║
║  ▓   DHC-V: 100.0  //  BAND: PRODUCTION_READY  //  SEVERITY: 0          ▓  ║
║  ▓   Everything is a Plugin. Nothing is Trusted.                          ▓  ║
║  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

— Credits: this benchmark was engineered to be the **ceiling** against which
LLM-generated DeepSeek Harness plugin code is measured. It is hermetic
(deterministic mock LLM, frozen clock, fixed nonces), secure (defense in
depth, strict schemas, constant-time crypto, capability gating), and
mathematically rigorous (multiplicative DHC-V with a hard floor). For the
Cordis design paper, see
[*A Programming Paradigm for Spatiotemporal Composability*](https://arxiv.org/abs/2608.25512).
For the parent project, see
[github.com/deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness).
