"""session_exporter_v1 — NDJSON exporter for C2 SessionEventLog.

Subscribes to `turn/end`. On each event, drains
`ctx.inject("sessions").get_history()` and writes one JSON object per
event to `<out_dir>/<turn_id>.ndjson`. Each line is independently
parseable, so the file can be split or streamed.

Configuration:
    {"out_dir": "~/.dhc/sessions", "include_heartbeats": false}

If the directory does not exist it is created. Write errors are
logged but do not crash the harness.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

from dhc.cordis.context import Context
from dhc.cordis.plugin import plugin


def _serialize_event(event) -> dict:
    return {"id": event.id, "type": event.type, "payload": event.payload}


@plugin("session_exporter_v1")
async def apply(ctx: Context, config: dict) -> Callable[[], None]:
    out_dir = Path(os.path.expanduser(config.get("out_dir", "~/.dhc/sessions")))

    async def on_turn_end(payload: dict) -> None:
        try:
            log = ctx.inject("sessions")
            if log is None:
                return
            turn_id = str(payload.get("turn_id") or f"turn-{int(time.time()*1000)}")
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"{turn_id}.ndjson"
            with path.open("w", encoding="utf-8") as fh:
                for ev in log.get_history():
                    fh.write(json.dumps(_serialize_event(ev), separators=(",", ":")) + "\n")
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger("dhc.session_exporter_v1").warning(
                "turn %s export failed: %s", payload.get("turn_id"), exc
            )

    ctx.events.on("turn/end", on_turn_end)

    async def dispose() -> None:
        ctx.events.off("turn/end", on_turn_end)

    return dispose
