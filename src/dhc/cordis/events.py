"""EventEmitter supporting async listeners, parallel emit, and waterfall dispatch."""

import asyncio
from typing import Any, Callable, Dict, List

Listener = Callable[..., Any]


class EventEmitter:
    def __init__(self) -> None:
        self._listeners: Dict[str, List[Listener]] = {}

    def on(self, event: str, listener: Listener) -> None:
        self._listeners.setdefault(event, []).append(listener)

    def off(self, event: str, listener: Listener) -> None:
        if event in self._listeners:
            self._listeners[event] = [l for l in self._listeners[event] if l is not listener]

    async def emit(self, event: str, *args: Any, **kwargs: Any) -> None:
        for listener in list(self._listeners.get(event, [])):
            if asyncio.iscoroutinefunction(listener):
                await listener(*args, **kwargs)
            else:
                listener(*args, **kwargs)

    async def waterfall(self, event: str, initial: Any, *args: Any, **kwargs: Any) -> Any:
        current = initial
        for listener in list(self._listeners.get(event, [])):
            if asyncio.iscoroutinefunction(listener):
                current = await listener(current, *args, **kwargs)
            else:
                current = listener(current, *args, **kwargs)
        return current
