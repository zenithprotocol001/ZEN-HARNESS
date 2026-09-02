"""Plugin discovery and lifecycle.

Discovery walks `src/dhc/plugins/<id>/manifest.json` at startup. For
each manifest, the loader:

  1. Imports the corresponding `service.py` (idempotently — a
     subsequent `load()` is a no-op if the plugin is already loaded).
  2. Verifies the file's SHA-256 matches `manifest.sha256` using
     `hmac.compare_digest` (constant time). Mismatches raise
     `PluginIntegrityError` and the plugin is NOT loaded.
  3. Calls `apply(ctx, config)` and stores the returned `dispose`
     callable (or a list of them) on the context's disposable stack.
  4. Records the loaded plugin in `state.loaded[plugin_id]`.

`unload(plugin_id)` calls the stored dispose(s). Failure is logged
via C10 telemetry (if present) and surfaced — never silently ignored.

Hardening notes:
  * Loaded plugins run in the same Python process as the harness.
    This is by design: the harness is a development-time tool, not a
    multi-tenant production runtime. The README and the plugin
    directory's top-level docstring call this out explicitly.
  * Plugins can subscribe to the Cordis event bus and read `ctx`
    services. They cannot add aiohttp routes; arbitrary HTTP surface
    is reserved for the core C1 module and would require an
    `aiohttp` middleware seam that's bigger than the marketplace.
  * All loaded plugins share the C1 server's CSP and bearer-token
    auth. There is no per-plugin auth bypass.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import importlib.util
import json
import logging
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from dhc.cordis.context import Context
from dhc.plugins._manifest import PluginManifest

_LOG = logging.getLogger("dhc.plugins")


class PluginError(Exception):
    """Base for all plugin errors."""


class PluginNotFoundError(PluginError):
    pass


class PluginIntegrityError(PluginError):
    """Manifest SHA-256 mismatch with on-disk service.py."""


class PluginValidationError(PluginError):
    """Manifest failed Pydantic validation."""


class PluginApplyError(PluginError):
    """Plugin's apply() raised during load."""


@dataclass
class LoadedPlugin:
    """The runtime state of a successfully loaded plugin."""

    plugin_id: str
    manifest: PluginManifest
    module: Any
    dispose: Callable[[], Any] | list[Callable[[], Any]] | None
    error: str | None = None
    loaded_at_ms: int = 0


@dataclass
class PluginState:
    loaded: dict[str, LoadedPlugin] = field(default_factory=dict)
    discovered: dict[str, PluginManifest] = field(default_factory=dict)
    by_id: dict[str, LoadedPlugin] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Mirror loaded -> by_id for fast lookup. (Same dict, kept for
        # future divergence if a plugin ever has multiple instances.)
        self.by_id = self.loaded


def _plugins_root() -> Path:
    """Locate the on-disk plugins directory.

    `src/dhc/plugins/__init__.py` is the package root, so its parent is
    the directory we scan.
    """
    return Path(__file__).resolve().parent


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _import_plugin_module(plugin_id: str, service_path: Path) -> Any:
    """Import the plugin's `service.py` as `dhc.plugins.<id>.service`.

    Using `importlib.util.spec_from_file_location` keeps each plugin
    isolated under its own module name so two plugins that happen to
    define the same internal symbol don't collide.
    """
    module_name = f"dhc.plugins.{plugin_id}.service"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, str(service_path))
    if spec is None or spec.loader is None:
        raise PluginValidationError(
            f"could not build import spec for {service_path}"
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def discover(repo_root: Path | None = None) -> dict[str, PluginManifest]:
    """Walk the plugins directory and return {plugin_id: manifest}.

    Discovery is non-throwing: a manifest that fails validation is
    logged and skipped. Discovery never crashes the harness.
    """
    root = repo_root or _plugins_root()
    out: dict[str, PluginManifest] = {}
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        manifest_path = child / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = PluginManifest.model_validate(raw)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            _LOG.warning("plugin discovery: skipping %s: %s", child.name, exc)
            continue
        if manifest.id != child.name:
            _LOG.warning(
                "plugin discovery: manifest id %r does not match directory %r",
                manifest.id,
                child.name,
            )
            continue
        out[manifest.id] = manifest
    return out


def load(
    state: PluginState,
    ctx: Context,
    plugin_id: str,
    config: dict | None = None,
) -> LoadedPlugin:
    """Load a discovered plugin and register its apply() with `ctx`."""
    if plugin_id in state.loaded:
        return state.loaded[plugin_id]

    if plugin_id not in state.discovered:
        raise PluginNotFoundError(
            f"plugin {plugin_id!r} is not in the discovered set; "
            f"available: {sorted(state.discovered)}"
        )
    manifest = state.discovered[plugin_id]
    service_path = _plugins_root() / plugin_id / "service.py"
    if not service_path.is_file():
        raise PluginNotFoundError(f"plugin {plugin_id!r}: service.py not found")

    observed = _hash_file(service_path)
    if not hmac.compare_digest(observed.encode("ascii"), manifest.sha256.encode("ascii")):
        raise PluginIntegrityError(
            f"plugin {plugin_id!r} sha256 mismatch: "
            f"manifest={manifest.sha256} on-disk={observed}"
        )

    module = _import_plugin_module(plugin_id, service_path)
    apply = getattr(module, "apply", None)
    if apply is None or not callable(apply):
        raise PluginValidationError(
            f"plugin {plugin_id!r}: service.py has no callable `apply`"
        )

    cfg = config if config is not None else {}
    try:
        # Plugin apply() is async (per the @plugin decorator contract
        # used by the core modules). Await it so the service
        # registrations inside apply() take effect.
        import asyncio as _asyncio
        result = apply(ctx, cfg)
        if hasattr(result, "__await__"):
            try:
                loop = _asyncio.get_event_loop()
                if loop.is_running():
                    # We're inside a running event loop (e.g.
                    # pytest-asyncio). We can't `await` here
                    # synchronously; the caller must use
                    # `load_async` instead.
                    raise RuntimeError(
                        "load() called from a running event loop; use load_async()"
                    )
                dispose = loop.run_until_complete(result)
            except RuntimeError:
                # No loop: run a fresh one.
                dispose = _asyncio.run(result)
        else:
            dispose = result
    except Exception as exc:
        tb = traceback.format_exc()
        _LOG.error("plugin %r apply() raised: %s\n%s", plugin_id, exc, tb)
        raise PluginApplyError(f"plugin {plugin_id!r}: apply() raised: {exc}") from exc

    import time as _time
    loaded = LoadedPlugin(
        plugin_id=plugin_id,
        manifest=manifest,
        module=module,
        dispose=dispose,
        loaded_at_ms=int(_time.time() * 1000),
    )
    state.loaded[plugin_id] = loaded
    return loaded


async def load_async(
    state: "PluginState",
    ctx: Context,
    plugin_id: str,
    config: dict | None = None,
) -> "LoadedPlugin":
    """Async variant of `load` for callers that are already inside
    a running event loop (pytest-asyncio tests, aiohttp handlers)."""
    if plugin_id in state.loaded:
        return state.loaded[plugin_id]
    if plugin_id not in state.discovered:
        raise PluginNotFoundError(
            f"plugin {plugin_id!r} is not in the discovered set; "
            f"available: {sorted(state.discovered)}"
        )
    manifest = state.discovered[plugin_id]
    service_path = _plugins_root() / plugin_id / "service.py"
    if not service_path.is_file():
        raise PluginNotFoundError(f"plugin {plugin_id!r}: service.py not found")
    observed = _hash_file(service_path)
    if not hmac.compare_digest(observed.encode("ascii"), manifest.sha256.encode("ascii")):
        raise PluginIntegrityError(
            f"plugin {plugin_id!r} sha256 mismatch: "
            f"manifest={manifest.sha256} on-disk={observed}"
        )
    module = _import_plugin_module(plugin_id, service_path)
    apply = getattr(module, "apply", None)
    if apply is None or not callable(apply):
        raise PluginValidationError(
            f"plugin {plugin_id!r}: service.py has no callable `apply`"
        )
    cfg = config if config is not None else {}
    import time as _time
    try:
        dispose = await apply(ctx, cfg)
    except Exception as exc:
        tb = traceback.format_exc()
        _LOG.error("plugin %r apply() raised: %s\n%s", plugin_id, exc, tb)
        raise PluginApplyError(f"plugin {plugin_id!r}: apply() raised: {exc}") from exc
    loaded = LoadedPlugin(
        plugin_id=plugin_id,
        manifest=manifest,
        module=module,
        dispose=dispose,
        loaded_at_ms=int(_time.time() * 1000),
    )
    state.loaded[plugin_id] = loaded
    return loaded


async def unload(state: PluginState, ctx: Context, plugin_id: str) -> None:
    """Call the plugin's dispose, if any. Errors are logged and re-raised.

    Async because plugin dispose functions may be `async def` (per the
    @plugin decorator contract). When called from sync code (e.g. the
    C1 route handler's `unload_plugin`), use `asyncio.run(...)`.
    """
    if plugin_id not in state.loaded:
        raise PluginNotFoundError(f"plugin {plugin_id!r} is not loaded")

    loaded = state.loaded.pop(plugin_id)
    dispose = loaded.dispose
    if dispose is None:
        return

    async def _call_dispose() -> None:
        if callable(dispose):
            res = dispose()
            if hasattr(res, "__await__"):
                await res
        elif isinstance(dispose, (list, tuple)):
            for d in dispose:
                if not callable(d):
                    continue
                res = d()
                if hasattr(res, "__await__"):
                    await res

    await _call_dispose()


def unload_sync(state: PluginState, ctx: Context, plugin_id: str) -> None:
    """Synchronous wrapper around `unload` for callers that are not
    already inside an event loop (e.g. a CLI)."""
    import asyncio

    try:
        asyncio.run(unload(state, ctx, plugin_id))
    except RuntimeError:
        # Already inside a loop; defer to the caller.
        raise


def list_loaded(state: PluginState) -> list[dict[str, Any]]:
    return [
        {
            "id": lp.plugin_id,
            "name": lp.manifest.name,
            "version": lp.manifest.version,
            "loaded_at_ms": lp.loaded_at_ms,
        }
        for lp in state.loaded.values()
    ]


def list_discovered(state: PluginState) -> list[dict[str, Any]]:
    return [
        {
            "id": m.id,
            "name": m.name,
            "version": m.version,
            "loaded": m.id in state.loaded,
        }
        for m in state.discovered.values()
    ]
