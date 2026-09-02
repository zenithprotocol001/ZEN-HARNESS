"""Tests for dhc.integrations.provider_client_for (v1.3.0 / ADR-0009)."""
from __future__ import annotations

import pytest

from dhc.integrations import provider_client_for
from dhc.integrations.anthropic_client import AnthropicClient
from dhc.integrations.base import LLMProvider, ProviderError
from dhc.integrations.openai_client import OpenAIClient
from dhc.integrations.openrouter_client import OpenRouterClient
from dhc.services.model_registry import Model


def test_factory_returns_openai_client() -> None:
    m = Model(
        id="openai/gpt-4o-mini", name="GPT-4o mini", provider="openai",
        context_length=128000, pricing_input=0.15, pricing_output=0.60,
    )
    client = provider_client_for(m)
    assert isinstance(client, OpenAIClient)
    assert isinstance(client, LLMProvider)
    assert client.provider_name == "openai"


def test_factory_returns_anthropic_client() -> None:
    m = Model(
        id="anthropic/claude-3-5-haiku-latest", name="Haiku", provider="anthropic",
        context_length=200000, pricing_input=0.8, pricing_output=4.0,
    )
    client = provider_client_for(m)
    assert isinstance(client, AnthropicClient)
    assert client.provider_name == "anthropic"


def test_factory_returns_openrouter_client() -> None:
    m = Model(
        id="openrouter/auto", name="Auto", provider="openrouter",
        context_length=200000, pricing_input=0, pricing_output=0,
    )
    client = provider_client_for(m)
    assert isinstance(client, OpenRouterClient)
    assert client.provider_name == "openrouter"


def test_factory_raises_for_unknown_provider() -> None:
    m = Model(
        id="bogus/x", name="X", provider="bogus",
        context_length=1, pricing_input=0, pricing_output=0,
    )
    with pytest.raises(ProviderError) as ei:
        provider_client_for(m)
    assert ei.value.provider == "bogus"
    assert "bogus" in str(ei.value)


def test_factory_rejects_mock_provider() -> None:
    """The mock surface is the v1.2.x C7 path; the factory must
    not return a client for it. The C7 wrapper is expected to
    short-circuit before calling the factory."""
    m = Model(
        id="mock-llm/default", name="Mock", provider="mock",
        context_length=8192, pricing_input=0, pricing_output=0,
    )
    with pytest.raises(ProviderError, match="no provider for 'mock-llm/default'"):
        provider_client_for(m)
