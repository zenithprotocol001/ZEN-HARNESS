"""v1.3.0 ModelRegistry (ADR-0006).

Hardcoded list of 6 models across 4 providers (mock + 3 live).
Per ADR-0006, dynamic discovery (OpenRouter) is deferred to v1.4.0.

The registry is a pure-Python singleton; no filesystem or network
access. The `scripts/invariants_check.ps1` v1.3.0 invariants
assert:
- the list is non-empty,
- every id matches `^[a-z0-9-]+/[a-z0-9.-]+$`,
- `model.provider` equals the part before `/`,
- `provider_client_for(m)` covers every provider in the registry.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


_ID_RE = re.compile(r"^[a-z0-9-]+/[a-z0-9.-]+$")


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    provider: str
    context_length: int
    pricing_input: float
    pricing_output: float
    capabilities: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not _ID_RE.match(self.id):
            raise ValueError(
                f"model id {self.id!r} does not match ^[a-z0-9-]+/[a-z0-9.-]+$"
            )
        if self.context_length <= 0:
            raise ValueError(f"model {self.id!r}: context_length must be > 0")
        if self.pricing_input < 0 or self.pricing_output < 0:
            raise ValueError(f"model {self.id!r}: pricing must be non-negative")


_HARDCODED: tuple[Model, ...] = (
    Model(
        id="mock-llm/default",
        name="Mock LLM (default)",
        provider="mock",
        context_length=8192,
        pricing_input=0.0,
        pricing_output=0.0,
        capabilities=frozenset(),
    ),
    Model(
        id="openai/gpt-4o-mini",
        name="GPT-4o mini",
        provider="openai",
        context_length=128_000,
        pricing_input=0.15,
        pricing_output=0.60,
        capabilities=frozenset({"tools", "json"}),
    ),
    Model(
        id="openai/gpt-4.1",
        name="GPT-4.1",
        provider="openai",
        context_length=1_000_000,
        pricing_input=2.00,
        pricing_output=8.00,
        capabilities=frozenset({"tools", "json", "vision"}),
    ),
    Model(
        id="anthropic/claude-3-5-sonnet-latest",
        name="Claude 3.5 Sonnet",
        provider="anthropic",
        context_length=200_000,
        pricing_input=3.00,
        pricing_output=15.00,
        capabilities=frozenset({"tools", "json", "vision"}),
    ),
    Model(
        id="anthropic/claude-3-5-haiku-latest",
        name="Claude 3.5 Haiku",
        provider="anthropic",
        context_length=200_000,
        pricing_input=0.80,
        pricing_output=4.00,
        capabilities=frozenset({"tools", "json"}),
    ),
    Model(
        id="openrouter/auto",
        name="OpenRouter Auto",
        provider="openrouter",
        context_length=200_000,
        pricing_input=0.0,
        pricing_output=0.0,
        capabilities=frozenset({"tools", "json"}),
    ),
)


class ModelRegistry:
    """Hardcoded registry of LLM models (ADR-0006).

    Pure-Python singleton. Construct freely; the constructor does
    no I/O. The list is frozen at import time.
    """

    def __init__(self) -> None:
        self._models: tuple[Model, ...] = _HARDCODED
        self._by_id: dict[str, Model] = {m.id: m for m in self._models}

    def list_models(self) -> list[Model]:
        return list(self._models)

    def get_model(self, model_id: str) -> Model | None:
        return self._by_id.get(model_id)

    def models_for_provider(self, provider: str) -> list[Model]:
        return [m for m in self._models if m.provider == provider]

    def __len__(self) -> int:
        return len(self._models)

    def __contains__(self, model_id: object) -> bool:
        return isinstance(model_id, str) and model_id in self._by_id


__all__ = ["Model", "ModelRegistry"]
