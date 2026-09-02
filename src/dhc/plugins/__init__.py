"""DHC plugin namespace.

Each subdirectory is a discoverable plugin:

    src/dhc/plugins/
        <plugin_id>/
            __init__.py
            manifest.json
            service.py          # exposes `async def apply(ctx, config)`

The loader (`loader.py`) scans this directory, verifies each plugin's
manifest against the on-disk SHA-256 of `service.py`, and registers
the plugin's `apply(ctx, config)` callable with the Cordis Context.
"""
