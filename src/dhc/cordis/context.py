"""Context: service registry, event bus, and disposable stack."""

import asyncio
from typing import Any, Callable, Dict

from dhc.cordis.events import EventEmitter


class Context:
    def __init__(self) -> None:
        self.events = EventEmitter()
        self.services: Dict[str, Any] = {}
        self._disposables: list[Callable[[], Any]] = []

    def provide(self, key: str, service: Any) -> None:
        self.services[key] = service

    def inject(self, key: str) -> Any:
        return self.services.get(key)

    def add_disposable(self, dispose_fn: Callable[[], Any]) -> None:
        self._disposables.append(dispose_fn)

    async def dispose(self) -> None:
        for fn in reversed(self._disposables):
            try:
                if asyncio.iscoroutinefunction(fn):
                    await fn()
                else:
                    fn()
            except Exception:
                ctx_logger = self.services.get("telemetry")
                if ctx_logger is not None and hasattr(ctx_logger, "log_event"):
                    ctx_logger.log_event("context/dispose_error", {"fn": getattr(fn, "__name__", repr(fn))})
        self._disposables.clear()
