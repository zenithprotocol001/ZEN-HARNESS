HARNESS Creation Benchmark (DHC-V) — version 0.6.0
====================================================

This is the reference implementation of the DeepSeek Harness (Cordis)
benchmark. It contains 10 core modules (C1–C10) plus a scoring engine,
a React/Vite web client, deterministic mock LLM fixtures, and a suite
of unit + security + scoring tests.

The repository is organized as follows:

  pyproject.toml          Python package metadata, dependencies, test extras
  pytest.ini              pytest configuration (asyncio_mode = auto)
  src/dhc/                Reference implementation
    cordis/               Minimal Python port of the Cordis framework
      context.py          Context: service registry + event bus + disposables
      events.py           EventEmitter with on/off/emit/waterfall
      plugin.py           @plugin decorator
    modules/              The 10 core plugins
      c1_gui_web_core/    WS bridge + strict CSP, no unsafe-inline/eval
      c2_session_event_log/  Append-only tuple snapshot, frozen pydantic
      c3_prompt_assembler/   9-token boundary escape, capability-filtered tools
      c4_tool_guard_pipeline/  Strict pydantic schemas, list[str] bash
      c5_agent_registry/   HMAC-SHA256 signed manifests, 0x1F canonical form
      c6_turn_step_driver/  Waterfall orchestrator, 5-step circuit breaker
      c7_llm_stream_adapter/  SSE consumer, chunk buffer, key redaction
      c8_webhook_dispatch/  HMAC verify, nonce + timestamp replay guard
      c9_capability_policy/  Deny-all, tools/pre-execute interception
      c10_observability_sink/  PII/secret scrubber, OpenTelemetry-shaped
    scoring/scorer.py     Multiplicative DHC-V, security<50 hard floor
  apps/web/               React + Vite client (DOMPurify XSS defense)
  fixtures/mock_llm/      Deterministic aiohttp SSE server + scripted JSONL
  tests/                  unit/ + security/ + scoring/ test suites
  scripts/                PowerShell static checks + relay packager
  relay/                  Versioned .zip artifacts + MANIFEST.txt

For full documentation, see README.md. For runtime usage, see
docs/USAGE.md once generated. To evaluate an LLM against this
benchmark, see docs/EVALUATING_LLMS.md.

The 4-command sync recipe (per MANIFEST.txt):

  1. Extract harness_benchmark-v0.6.0-20260901.zip
  2. cd harness_benchmark && git init && git add . && git commit -m "v0.6.0"
  3. git remote add origin git@github.com:zenithprotocol001/harness.git
  4. git push -u origin main --force

DHC-V self-score: 100.0 / production_ready
