"""Tests for dhc.integrations.base (v1.3.0 / ADR-0008, ADR-0009)."""
from __future__ import annotations

import dataclasses

import pytest

from dhc.integrations.base import LLMProvider, ProviderError, RetryConfig


def test_retry_config_defaults() -> None:
    rc = RetryConfig()
    assert rc.max_attempts == 3
    assert rc.backoff_seconds == (1.0, 2.0)
    assert rc.retry_on_5xx is True
    assert rc.retry_on_network_error is True
    assert rc.retry_on_4xx is False
    assert rc.connect_timeout_s == 5.0
    assert rc.read_timeout_s == 30.0


def test_retry_config_is_frozen() -> None:
    rc = RetryConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        rc.max_attempts = 5  # type: ignore[misc]


def test_retry_config_max_attempts_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        RetryConfig(max_attempts=0, backoff_seconds=())


def test_retry_config_backoff_length_must_match_attempts() -> None:
    with pytest.raises(ValueError, match="backoff_seconds"):
        RetryConfig(max_attempts=3, backoff_seconds=(1.0,))  # need 2 entries


def test_provider_error_carries_status() -> None:
    e = ProviderError("x", status=401, provider="openai", model="openai/gpt-4o-mini")
    assert e.status == 401
    assert e.provider == "openai"
    assert e.model == "openai/gpt-4o-mini"
    assert str(e) == "x"


def test_provider_error_default_status_is_none() -> None:
    e = ProviderError("x")
    assert e.status is None
    assert e.provider == ""
    assert e.model == ""


def test_llm_provider_cannot_instantiate_directly() -> None:
    with pytest.raises(TypeError):
        LLMProvider()  # type: ignore[abstract]
