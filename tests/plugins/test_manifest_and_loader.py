"""Tests for the plugin manifest spec and the loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dhc.cordis.context import Context
from dhc.plugins._manifest import PluginManifest
from dhc.plugins.loader import (
    PluginApplyError,
    PluginIntegrityError,
    PluginNotFoundError,
    PluginState,
    PluginValidationError,
    discover,
    load_async,
    unload,
)


# ---------- manifest ----------


def test_manifest_happy_path():
    m = PluginManifest(
        id="hello_v1",
        name="Hello",
        version="0.1.0",
        author="DHC",
        description="a friendly plugin",
        sha256="0" * 64,
    )
    assert m.id == "hello_v1"
    assert m.version == "0.1.0"


def test_manifest_rejects_extra_fields():
    with pytest.raises(Exception):
        PluginManifest.model_validate(
            {
                "id": "hello_v1",
                "name": "Hello",
                "version": "0.1.0",
                "author": "DHC",
                "description": "x",
                "sha256": "0" * 64,
                "rogue": "no",
            }
        )


def test_manifest_rejects_bad_sha256():
    with pytest.raises(Exception):
        PluginManifest.model_validate(
            {
                "id": "hello_v1",
                "name": "Hello",
                "version": "0.1.0",
                "author": "DHC",
                "description": "x",
                "sha256": "not_hex",
            }
        )


def test_manifest_rejects_non_semver():
    with pytest.raises(Exception):
        PluginManifest.model_validate(
            {
                "id": "hello_v1",
                "name": "Hello",
                "version": "1.0",
                "author": "DHC",
                "description": "x",
                "sha256": "0" * 64,
            }
        )


def test_manifest_rejects_uppercase_id():
    with pytest.raises(Exception):
        PluginManifest.model_validate(
            {
                "id": "Hello_v1",
                "name": "Hello",
                "version": "0.1.0",
                "author": "DHC",
                "description": "x",
                "sha256": "0" * 64,
            }
        )


# ---------- discovery ----------


def test_discover_finds_the_five_bundled_plugins():
    manifests = discover()
    expected = {
        "rate_limiter_v1",
        "session_exporter_v1",
        "model_router_v1",
        "memory_store_v1",
        "prompt_browser_v1",
    }
    assert expected.issubset(manifests), f"missing: {expected - manifests}"
    for pid in expected:
        m = manifests[pid]
        assert m.id == pid
        # sha256 must be exactly 64 lowercase hex chars
        assert len(m.sha256) == 64
        int(m.sha256, 16)  # parses as hex


# ---------- loader ----------


async def test_load_and_unload_round_trip():
    from dhc.plugins.loader import load_async

    state = PluginState()
    state.discovered = discover()
    ctx = Context()
    lp = await load_async(state, ctx, "rate_limiter_v1", config={"rate": 2.0, "burst": 5.0})
    assert lp.plugin_id == "rate_limiter_v1"
    assert "rate_limiter" in ctx.services
    limiter = ctx.inject("rate_limiter")
    limiter.check("a1")  # should not raise
    await unload(state, ctx, "rate_limiter_v1")
    assert "rate_limiter" not in ctx.services


async def test_load_raises_on_integrity_mismatch(tmp_path, monkeypatch):
    """If the manifest's sha256 doesn't match the on-disk service.py,
    the loader must refuse to load it."""
    # Create a fake plugin directory with a tampered manifest.
    fake = tmp_path / "tampered_v1"
    fake.mkdir()
    (fake / "service.py").write_text("async def apply(ctx, config): return lambda: None\n")
    bad_manifest = {
        "id": "tampered_v1",
        "name": "Tampered",
        "version": "0.1.0",
        "author": "test",
        "description": "tampered",
        "sha256": "f" * 64,  # does not match the on-disk file
    }
    (fake / "manifest.json").write_text(json.dumps(bad_manifest))

    # Point the loader at our fake dir.
    from dhc.plugins import loader as loader_mod

    monkeypatch.setattr(loader_mod, "_plugins_root", lambda: tmp_path)

    state = PluginState()
    state.discovered = discover()
    ctx = Context()
    with pytest.raises(PluginIntegrityError):
        await load_async(state, ctx, "tampered_v1", config={})


async def test_load_raises_on_missing_apply(tmp_path, monkeypatch):
    """If service.py has no `apply` symbol, the loader must raise PluginValidationError."""
    import hashlib

    fake = tmp_path / "noop_v1"
    fake.mkdir()
    service = fake / "service.py"
    service.write_text("# no apply symbol here\n")
    sha = hashlib.sha256(service.read_bytes()).hexdigest()
    (fake / "manifest.json").write_text(
        json.dumps(
            {
                "id": "noop_v1",
                "name": "Noop",
                "version": "0.1.0",
                "author": "test",
                "description": "no apply",
                "sha256": sha,
            }
        )
    )

    from dhc.plugins import loader as loader_mod

    monkeypatch.setattr(loader_mod, "_plugins_root", lambda: tmp_path)

    state = PluginState()
    state.discovered = discover()
    ctx = Context()
    with pytest.raises(PluginValidationError):
        await load_async(state, ctx, "noop_v1", config={})


async def test_load_raises_on_apply_exception(tmp_path, monkeypatch):
    import hashlib

    fake = tmp_path / "broken_v1"
    fake.mkdir()
    service = fake / "service.py"
    service.write_text("async def apply(ctx, config): raise RuntimeError('boom')\n")
    sha = hashlib.sha256(service.read_bytes()).hexdigest()
    (fake / "manifest.json").write_text(
        json.dumps(
            {
                "id": "broken_v1",
                "name": "Broken",
                "version": "0.1.0",
                "author": "test",
                "description": "raises",
                "sha256": sha,
            }
        )
    )

    from dhc.plugins import loader as loader_mod

    monkeypatch.setattr(loader_mod, "_plugins_root", lambda: tmp_path)

    state = PluginState()
    state.discovered = discover()
    ctx = Context()
    with pytest.raises(PluginApplyError):
        await load_async(state, ctx, "broken_v1", config={})


async def test_load_is_idempotent():
    state = PluginState()
    state.discovered = discover()
    ctx = Context()
    await load_async(state, ctx, "rate_limiter_v1", config={})
    # Second load of the same id is a no-op.
    lp2 = await load_async(state, ctx, "rate_limiter_v1", config={})
    assert state.loaded["rate_limiter_v1"] is lp2


async def test_unload_unknown_raises():
    state = PluginState()
    ctx = Context()
    with pytest.raises(PluginNotFoundError):
        await unload(state, ctx, "nonexistent_v1")
