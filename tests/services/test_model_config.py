"""Tests for dhc.services.model_config (ADR-0011)."""
from __future__ import annotations

from pathlib import Path

import pytest

from dhc.cordis.secrets import SecretsService
from dhc.services.model_config import ModelConfig, ModelConfigStore


# ---------- ModelConfig validation ----------


def test_model_config_default_values():
    cfg = ModelConfig()
    assert cfg.temperature == 0.7
    assert cfg.max_tokens == 4096
    assert cfg.top_p == 1.0
    assert cfg.system_prompt == "You are a helpful assistant."


def test_model_config_validation_temperature():
    with pytest.raises(ValueError, match="temperature"):
        ModelConfig(temperature=-0.1)
    with pytest.raises(ValueError, match="temperature"):
        ModelConfig(temperature=2.1)
    # Boundaries accepted
    ModelConfig(temperature=0.0)
    ModelConfig(temperature=2.0)


def test_model_config_validation_max_tokens():
    with pytest.raises(ValueError, match="max_tokens"):
        ModelConfig(max_tokens=0)
    with pytest.raises(ValueError, match="max_tokens"):
        ModelConfig(max_tokens=8193)
    # Non-int rejected
    with pytest.raises(ValueError, match="max_tokens"):
        ModelConfig(max_tokens=1.5)  # type: ignore[arg-type]


def test_model_config_validation_top_p():
    with pytest.raises(ValueError, match="top_p"):
        ModelConfig(top_p=-0.1)
    with pytest.raises(ValueError, match="top_p"):
        ModelConfig(top_p=1.1)


def test_model_config_to_from_dict_round_trip():
    cfg = ModelConfig(temperature=0.3, max_tokens=1024, top_p=0.9, system_prompt="be terse")
    d = cfg.to_dict()
    assert d == {
        "temperature": 0.3,
        "max_tokens": 1024,
        "top_p": 0.9,
        "system_prompt": "be terse",
    }
    cfg2 = ModelConfig.from_dict(d)
    assert cfg2 == cfg


def test_model_config_from_dict_uses_defaults_for_missing_keys():
    cfg = ModelConfig.from_dict({})
    assert cfg == ModelConfig()


# ---------- ModelConfigStore round-trip ----------


def test_model_config_store_round_trip(tmp_path: Path):
    secrets = SecretsService(tmp_path)
    store = ModelConfigStore(secrets)
    sid = "abc-123"
    cfg = ModelConfig(temperature=0.2, max_tokens=512, top_p=0.8, system_prompt="hi")
    store.set_config(sid, cfg)
    assert store.get_config(sid) == cfg


def test_model_config_store_missing_returns_default(tmp_path: Path):
    secrets = SecretsService(tmp_path)
    store = ModelConfigStore(secrets)
    # No config stored; get returns the default.
    assert store.get_config("never-set") == ModelConfig()


def test_model_config_store_overwrites(tmp_path: Path):
    secrets = SecretsService(tmp_path)
    store = ModelConfigStore(secrets)
    sid = "sid-1"
    store.set_config(sid, ModelConfig(temperature=0.1))
    store.set_config(sid, ModelConfig(temperature=0.9))
    assert store.get_config(sid).temperature == 0.9


def test_model_config_store_rejects_empty_session_id():
    secrets = SecretsService(Path("."))
    store = ModelConfigStore(secrets)
    with pytest.raises(ValueError):
        store.get_config("")
    with pytest.raises(ValueError):
        store.set_config("", ModelConfig())


def test_model_config_store_corrupt_blob_returns_default(tmp_path: Path):
    """A non-JSON blob at the config key yields the default
    rather than crashing (defense in depth: a tampered log
    shouldn't lock the user out of their session)."""
    secrets = SecretsService(tmp_path)
    # Plant a non-JSON blob at the config key.
    secrets.put_raw("model_config_corrupt", b"not-json")
    store = ModelConfigStore(secrets)
    assert store.get_config("corrupt") == ModelConfig()
