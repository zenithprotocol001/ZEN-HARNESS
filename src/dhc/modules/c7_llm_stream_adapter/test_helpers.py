"""Async bytes stream that yields pre-fragmented chunks for SSE tests."""

from __future__ import annotations

from typing import AsyncIterator


async def fragmented_bytes(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for c in chunks:
        yield c
