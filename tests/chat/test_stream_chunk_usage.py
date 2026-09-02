"""Tests for dhc.modules.c7_llm_stream_adapter.service.StreamChunk (v1.3.1)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from dhc.modules.c7_llm_stream_adapter.service import StreamChunk


def test_stream_chunk_default_usage_is_none():
    """A StreamChunk constructed without `usage` has `usage=None`."""
    c = StreamChunk(delta="hi")
    assert c.usage is None
    # delta, tool_calls, finish_reason, raw_index are unchanged.
    assert c.delta == "hi"
    assert c.tool_calls == []
    assert c.finish_reason is None
    assert c.raw_index == 0


def test_stream_chunk_usage_field_accepts_dict():
    """`usage` accepts a dict with prompt/completion/total token counts."""
    c = StreamChunk(
        delta="",
        finish_reason="stop",
        usage={
            "prompt_tokens": 12,
            "completion_tokens": 7,
            "total_tokens": 19,
        },
    )
    assert c.usage == {
        "prompt_tokens": 12,
        "completion_tokens": 7,
        "total_tokens": 19,
    }


def test_stream_chunk_is_still_frozen_with_usage():
    """v1.3.1 doesn't relax the frozen contract: StreamChunk is
    still immutable even with `usage` set."""
    c = StreamChunk(usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3})
    with pytest.raises(ValidationError):
        c.delta = "tampered"  # type: ignore[misc]
