"""prompt_browser_v1 — read the eval master prompts and expose them.

The plugin is the data source for the React Prompts tab. It scans
`src/dhc/eval/prompts/*.txt` at load time and exposes the contents
via `ctx.inject("prompt_browser")` so the C1 /prompts route can
serialize them. There are no event subscriptions: the prompt text
is static for the lifetime of the server.

Errors during the scan (missing directory, unreadable file) are
logged via C10 telemetry if available and the plugin is loaded
with an empty prompt list — it never crashes the harness.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from dhc.cordis.context import Context
from dhc.cordis.plugin import plugin

_LOG = logging.getLogger("dhc.prompt_browser_v1")


class PromptBrowser:
    """Read-only catalog of master prompts for the GUI Prompts tab."""

    def __init__(self, prompts_dir: Path) -> None:
        self._dir = prompts_dir
        self._cache: dict[str, str] | None = None

    def _load(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if not self._dir.is_dir():
            return out
        for p in sorted(self._dir.glob("*.txt")):
            try:
                out[p.stem] = p.read_text(encoding="utf-8")
            except OSError as exc:
                _LOG.warning("could not read prompt %s: %s", p, exc)
        return out

    def list(self) -> list[dict[str, str | int]]:
        if self._cache is None:
            self._cache = self._load()
        return [
            {"key": k, "name": k.replace("_", " "), "length": len(v)}
            for k, v in self._cache.items()
        ]

    def get(self, key: str) -> str | None:
        if self._cache is None:
            self._cache = self._load()
        return self._cache.get(key)


def _prompts_dir() -> Path:
    # src/dhc/plugins/prompt_browser_v1/service.py
    #   -> src/dhc/plugins/prompt_browser_v1/  (parents[0])
    #   -> src/dhc/plugins/                     (parents[1])
    #   -> src/dhc/                             (parents[2])
    #   -> src/                                 (parents[3])
    #   -> <repo_root>                          (parents[4])
    # The prompts live at <repo_root>/src/dhc/eval/prompts.
    here = Path(__file__).resolve()
    repo_root = here.parents[4]
    return repo_root / "src" / "dhc" / "eval" / "prompts"


@plugin("prompt_browser_v1")
async def apply(ctx: Context, config: dict) -> Callable[[], None]:
    browser = PromptBrowser(prompts_dir=_prompts_dir())
    # Pre-warm cache.
    browser.list()
    ctx.provide("prompt_browser", browser)

    async def dispose() -> None:
        ctx.services.pop("prompt_browser", None)

    return dispose
