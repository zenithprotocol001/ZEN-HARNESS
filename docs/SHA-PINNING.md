# Plugin SHA-256 Pinning

The plugin marketplace verifies the integrity of every plugin at load
time by comparing the SHA-256 of `service.py` against the value in
`manifest.json` using `hmac.compare_digest`. A mismatch is a hard
error: the plugin will not load.

The 5 currently bundled plugins are **pinned** below. If you change
any `service.py`, you must:

1. Run `python -m dhc.plugins._sha <plugin_id>`.
2. Copy the new digest into the `sha256` field of `manifest.json`.
3. Update this file.

`scripts/invariants_check.ps1` asserts that each `manifest.json`
contains a 64-character lowercase hex `sha256` field and that the
field matches the value recorded in this file.

## Currently locked SHAs

| Plugin | SHA-256 | Manifest field verified? |
|---|---|---|
| `rate_limiter_v1` | `7601a11d5c671fe6abb6bbed55b047a5f3c6d4c053eace5e93cfc8b35409d6d8` | yes |
| `session_exporter_v1` | `35e6985c9ee8dee4b47e49a01d4daa4f817d933cf3b09605ded754d1a2367c75` | yes |
| `model_router_v1` | `e459a03e6e469324b3d93889a1fddf8645d840d02bea21ea5c22a007b0be2fa2` | yes |
| `memory_store_v1` | `ccea4ff8e3d680f3a2f1bc76cc5fac8b6a837507a26663bb3203a4ac3b003d8b` | yes |
| `prompt_browser_v1` | `b33c95c21e7617e773c117a2e730b8cf87c82823bb96f60b61c044aa78f1df85` | yes |

## Recompute

From the repo root:

```bash
python -m dhc.plugins._sha rate_limiter_v1
python -m dhc.plugins._sha session_exporter_v1
python -m dhc.plugins._sha model_router_v1
python -m dhc.plugins._sha memory_store_v1
python -m dhc.plugins._sha prompt_browser_v1
```

Each prints the lowercase hex digest on stdout.

## History

- **2026-09-01 — initial lock** (5 plugins, all at v0.1.0).
