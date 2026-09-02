# ADR-0009 — Provider Abstraction

- **Status**: Accepted (2026-09-02)
- **Deciders**: harness maintainers
- **Date**: 2026-09-02
- **Supersedes**: (none)
- **Related**: ADR-0006 (Model Selection), ADR-0007 (API Key Management), ADR-0008 (Retry Policy)

## Context and problem statement

v1.3.0 needs to support 3 live LLM providers (OpenAI, Anthropic, OpenRouter) with different request shapes, different SSE event sequences, and different auth headers. The C7 `LLMStreamAdapter` already exists for the v1.2.0 chat surface (OpenAI-compatible POST + SSE); we must not break it.

We need to lock:

- The interface that all 3 providers implement.
- Where the mock LLM fits (it predates this ADR).
- How the C7 wrapper dispatches between the mock and the live providers.
- What invariants a new provider must satisfy to be added in the future.

## Decision drivers

- The v1.2.0 chat surface and its 8 mock tests must keep passing without modification.
- The C7 wrapper is the integration seam; provider clients are isolated and testable on their own.
- Each provider has a different SSE shape. Pushing the parsing into the client (not the wrapper) keeps the wrapper thin.
- Adding a 4th provider in the future (e.g. Google Gemini) should require only one new file in `src/dhc/integrations/` and one entry in the factory.

## Considered options

### Option A — One mega-class with provider-specific branches

`LLMStreamAdapter.chat_stream` checks `model.startswith("gpt-")`, `model.startswith("claude-")`, etc., and constructs the right request inline. Pros: no new files. Cons: the v1.2.x test surface has to be re-tested against every branch; adding a 4th provider means editing C7.

### Option B (chosen) — `LLMProvider` ABC + concrete subclasses + factory

Each provider is its own class implementing `LLMProvider.chat_stream`. The factory `provider_client_for(model)` dispatches by `model.provider`. The C7 wrapper resolves the model and key, calls the factory, and forwards the stream. The mock LLM stays outside the ABC (it's a v1.2.x artifact); the C7 wrapper short-circuits on `model == "mock-llm/default"` before calling the factory.

### Option C — Strategy pattern with a registry

Each provider registers itself with a global registry. The factory walks the registry. Pros: open for extension without editing the factory. Cons: introduces module-level state and import-order coupling; the harness has no such convention.

## Decision

**Option B.** The `LLMProvider` ABC is the single interface every live provider implements. The factory `provider_client_for(model)` is the only dispatch point. The C7 wrapper is a thin layer that resolves the model and key, calls the factory, and yields the result.

Locked shape (frozen contract — see `docs/v1.3.0-technical-spec.md` § 2.2):

```python
class LLMProvider(ABC):
    provider_name: ClassVar[str]  # "openai" | "anthropic" | "openrouter"

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        api_key: str,
        retry_config: RetryConfig | None = None,
    ) -> AsyncIterator[StreamChunk]: ...
```

The `StreamChunk` is the v1.2.0 frozen pydantic model at `src/dhc/modules/c7_llm_stream_adapter/service.py:45`. No new fields, no aliasing.

Provider mapping (locked by this ADR + the `ModelRegistry` constant):

| `model.provider` | Client class | File |
|---|---|---|
| `openai` | `OpenAIClient` | `src/dhc/integrations/openai_client.py` |
| `anthropic` | `AnthropicClient` | `src/dhc/integrations/anthropic_client.py` |
| `openrouter` | `OpenRouterClient` | `src/dhc/integrations/openrouter_client.py` |
| `mock` | (none — C7 short-circuits) | n/a |
| anything else | raises `ProviderError` | n/a |

The C7 wrapper's `chat_stream(messages, model)` is **extended** (not replaced) with optional `model_registry` and `secrets_service` parameters. When both are set AND the model is not `mock-llm/default`, the wrapper does the dispatch. When either is unset, the wrapper falls through to the v1.2.x behavior (POST to `base_url/v1/chat/completions`). The 8 v1.2.x mock tests construct C7 with no registry or secrets and continue to work.

## Consequences

- **Positive**: adding a 4th provider in the future is a single new file + a 2-line factory update + a new test file. The C7 wrapper is unchanged.
- **Positive**: provider clients are unit-testable in isolation. The 30 tests across the 3 client files (10 + 10 + 5 + 5 factory) cover the full surface.
- **Positive**: the mock LLM is a v1.2.x artifact and is not dragged into the new abstraction. The 8 v1.2.x mock tests are untouched.
- **Negative**: the C7 wrapper has two execution modes (mock vs. dispatch). The dispatch path is tested by the 10 `test_c7_dispatch.py` tests; the mock path is tested by the existing 8 mock tests. The risk is a future edit that breaks the v1.2.x path; the `scripts/invariants_check.ps1` "v1.2.x compat" invariant guards this.
- **Negative**: the `StreamChunk` shape is frozen, so v1.3.0 cannot add a `usage` field for token counting. Deferred to v1.3.1 as a non-breaking additive field (per `docs/v1.3.0-technical-spec.md` § 2.1).
- **Risk**: a future provider that does not support SSE (e.g. a hypothetical batch API) cannot fit this ABC. Mitigation: this is a chat surface, not a batch surface. v1.4.0 can add a `LLMProvider.batch_complete` method if needed.

## Compliance

- The `scripts/invariants_check.ps1` adds a new check: every `LLMProvider` subclass in `src/dhc/integrations/` has a corresponding test file in `tests/integrations/` that contains a `test_*_chat_stream_*` test. The check enumerates the integration test files and asserts each subclass has at least one matching test.
- The `scripts/invariants_check.ps1` also asserts that `provider_client_for` covers every provider listed in the `ModelRegistry`. Adding a model whose provider is not in the factory fails the build.
- A "v1.2.x compat" invariant asserts the 8 `test_mock_llm.py` tests still import and pass; if a future edit breaks the mock path, this fails.
- The provider client imports `StreamChunk` from `dhc.modules.c7_llm_stream_adapter.service`, **not** from a local copy. This is enforced by an import-shape check.
