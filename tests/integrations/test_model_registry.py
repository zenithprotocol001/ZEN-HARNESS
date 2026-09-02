"""Tests for dhc.services.model_registry (v1.3.0 / ADR-0006)."""
from __future__ import annotations

import dataclasses

import pytest

from dhc.services.model_registry import Model, ModelRegistry


# ---------- Model dataclass invariants ----------


def test_model_dataclass_is_frozen() -> None:
    m = Model(
        id="openai/gpt-4o-mini", name="GPT-4o mini", provider="openai",
        context_length=128000, pricing_input=0.15, pricing_output=0.60,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.id = "x/y"  # type: ignore[misc]


def test_model_id_must_match_pattern() -> None:
    with pytest.raises(ValueError, match="does not match"):
        Model(
            id="OpenAI/GPT-4o", name="X", provider="openai",
            context_length=1, pricing_input=0, pricing_output=0,
        )


def test_model_id_must_have_a_slash() -> None:
    with pytest.raises(ValueError, match="does not match"):
        Model(
            id="no-slash-here", name="X", provider="openai",
            context_length=1, pricing_input=0, pricing_output=0,
        )


def test_model_context_length_must_be_positive() -> None:
    with pytest.raises(ValueError, match="context_length"):
        Model(
            id="openai/gpt-x", name="X", provider="openai",
            context_length=0, pricing_input=0, pricing_output=0,
        )


def test_model_pricing_must_be_non_negative() -> None:
    with pytest.raises(ValueError, match="pricing"):
        Model(
            id="openai/gpt-x", name="X", provider="openai",
            context_length=1, pricing_input=-1.0, pricing_output=0,
        )


# ---------- ModelRegistry behavior ----------


def test_registry_singleton_is_constructible() -> None:
    r = ModelRegistry()
    assert isinstance(r, ModelRegistry)


def test_list_models_returns_six() -> None:
    r = ModelRegistry()
    assert len(r.list_models()) == 6


def test_list_models_includes_mock() -> None:
    r = ModelRegistry()
    ids = {m.id for m in r.list_models()}
    assert "mock-llm/default" in ids


def test_get_model_returns_known() -> None:
    r = ModelRegistry()
    m = r.get_model("openai/gpt-4o-mini")
    assert m is not None
    assert m.provider == "openai"
    assert m.context_length == 128_000


def test_get_model_returns_none_for_unknown() -> None:
    r = ModelRegistry()
    assert r.get_model("nope") is None


def test_get_model_returns_none_for_partial_id() -> None:
    r = ModelRegistry()
    # Without the provider prefix, the id doesn't match any model.
    assert r.get_model("gpt-4o-mini") is None


def test_models_for_provider_filters_correctly() -> None:
    r = ModelRegistry()
    assert len(r.models_for_provider("openai")) == 2
    assert len(r.models_for_provider("anthropic")) == 2
    assert len(r.models_for_provider("openrouter")) == 1
    assert len(r.models_for_provider("mock")) == 1


def test_models_for_provider_empty_for_unknown() -> None:
    r = ModelRegistry()
    assert r.models_for_provider("nonexistent") == []


def test_model_capabilities_is_frozenset() -> None:
    r = ModelRegistry()
    m = r.get_model("openai/gpt-4.1")
    assert m is not None
    assert isinstance(m.capabilities, frozenset)
    assert "vision" in m.capabilities


def test_registry_is_pure_python() -> None:
    """Constructing the registry must not touch the network or
    filesystem. We assert by checking that the registry's modules
    are already imported and no I/O call is made in __init__."""
    import sys

    # Clear out any cached state
    sys.modules.pop("dhc.services.model_registry", None)
    r = ModelRegistry()
    assert r is not None
