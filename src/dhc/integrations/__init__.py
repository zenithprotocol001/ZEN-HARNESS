"""v1.3.0 live LLM provider clients (ADR-0009).

The factory `provider_client_for(model)` dispatches to the right
concrete client. The mock LLM is not a client; the C7 wrapper
short-circuits before calling this factory.

Concrete clients land in v1.3.0 Phase 3:
- `OpenAIClient`
- `AnthropicClient`
- `OpenRouterClient`
"""
from __future__ import annotations

from dhc.integrations.base import LLMProvider, ProviderError
from dhc.services.model_registry import Model


def provider_client_for(model: Model) -> LLMProvider:
    """Return a provider client for the given model.

    The caller (C7 dispatch) is responsible for short-circuiting on
    `model.provider == "mock"` before calling this — the mock
    surface is the v1.2.x C7 path, not a live provider.

    Raises `ProviderError` if the provider is unknown.
    """
    # Lazy imports so the base module stays importable without
    # the (large) provider HTTP machinery in scope.
    if model.provider == "openai":
        from dhc.integrations.openai_client import OpenAIClient

        return OpenAIClient()
    if model.provider == "anthropic":
        from dhc.integrations.anthropic_client import AnthropicClient

        return AnthropicClient()
    if model.provider == "openrouter":
        from dhc.integrations.openrouter_client import OpenRouterClient

        return OpenRouterClient()
    raise ProviderError(
        f"no provider for {model.id!r} (provider={model.provider!r})",
        provider=model.provider,
        model=model.id,
    )


__all__ = ["LLMProvider", "ProviderError", "provider_client_for"]
