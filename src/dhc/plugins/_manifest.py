"""Plugin manifest spec.

Every plugin under `src/dhc/plugins/<id>/` ships a `manifest.json` that
the loader reads to verify the plugin's identity, file integrity, and
declared capabilities. The on-disk `service.py` is SHA-256-hashed and
compared against `manifest.sha256` before any `apply()` call is made.

Hardening:
  * `extra="forbid"` and `strict=True` reject unknown fields and
    coerce-type attempts (e.g. an int slipped in for `sha256`).
  * `sha256` must be exactly 64 lowercase hex chars.
  * `id` is constrained to a conservative charset (lowercase letters,
    digits, underscore, dash) so it cannot collide with URL path
    segments, escape a path, or smuggle whitespace.
  * `version` is pinned to a `Literal` so a downgrade attack cannot
    silently install an older manifest.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

_PLUGIN_ID_RE = re.compile(r"^[a-z0-9_][a-z0-9_\-]{0,63}$")


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(pattern=_PLUGIN_ID_RE.pattern)
    name: str = Field(min_length=1, max_length=64)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    author: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=512)
    requires: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    config_schema: dict = Field(default_factory=dict)
