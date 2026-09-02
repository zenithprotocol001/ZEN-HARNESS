"""Tests for dhc.services.session_manager."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from dhc.services.session_manager import (
    MAX_MESSAGES_PER_SESSION,
    Session,
    SessionManager,
    _autotitle,
    _new_id,
)


# ---------- helpers ----------


def test_new_id_format():
    sid = _new_id()
    assert sid.startswith("s_")
    # `s_` is 2 chars, plus 16 hex chars = 18 total.
    assert len(sid) == 2 + 16
    # The 16 chars after the prefix are lowercase hex.
    int(sid[2:], 16)


def test_autotitle_basic():
    assert _autotitle("") == "New session"
    assert _autotitle("Hello world") == "Hello world"
    assert _autotitle("a\nb\nc") == "a b c"


def test_autotitle_truncates_to_word_count():
    text = " ".join(f"w{i}" for i in range(50))
    title = _autotitle(text)
    assert title.count(" ") == 7  # 8 words, 7 spaces
    assert title == " ".join(f"w{i}" for i in range(8))


def test_autotitle_truncates_to_char_limit():
    text = "x" * 200
    title = _autotitle(text)
    assert len(title) <= 60


def test_autotitle_collapses_whitespace():
    assert _autotitle("   multiple    spaces   here   ") == "multiple spaces here"


# ---------- create / get ----------


def test_create_get(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    s = mgr.create()
    assert s.id.startswith("s_")
    assert s.title == "New session"
    assert s.created_at > 0
    assert s.messages == []
    fetched = mgr.get(s.id)
    assert fetched is not None
    assert fetched.id == s.id
    assert fetched.title == "New session"


def test_get_missing_returns_none(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    assert mgr.get("s_does_not_exist") is None


def test_create_with_explicit_title(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    s = mgr.create(title="My custom title")
    assert s.title == "My custom title"
    assert mgr.get(s.id).title == "My custom title"


def test_sessions_persist_across_instances(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    s = mgr.create(title="hello")
    mgr2 = SessionManager(tmp_path)
    assert mgr2.get(s.id) is not None
    assert mgr2.get(s.id).title == "hello"


# ---------- append_message ----------


def test_append_message_basic(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    s = mgr.create()
    m = mgr.append_message(s.id, "user", "Hello there")
    assert m is not None
    assert m["role"] == "user"
    assert m["content"] == "Hello there"
    assert "id" in m
    assert "ts_ms" in m
    s2 = mgr.get(s.id)
    assert len(s2.messages) == 1
    assert s2.messages[0]["id"] == m["id"]


def test_append_message_auto_title(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    s = mgr.create()
    mgr.append_message(s.id, "user", "How do I install the dhc plugin?")
    s2 = mgr.get(s.id)
    assert s2.title == "How do I install the dhc plugin?"


def test_append_message_no_auto_title_when_set(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    s = mgr.create(title="My custom")
    mgr.append_message(s.id, "user", "What is X?")
    s2 = mgr.get(s.id)
    assert s2.title == "My custom"


def test_append_message_invalid_role(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    s = mgr.create()
    with pytest.raises(ValueError):
        mgr.append_message(s.id, "hacker", "evil")


def test_append_message_missing_session(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    assert mgr.append_message("s_missing", "user", "hi") is None


def test_append_message_with_tool_calls_and_tokens(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    s = mgr.create()
    m = mgr.append_message(
        s.id,
        "assistant",
        "ok",
        tool_calls=[{"name": "web_search", "args": {"q": "x"}}],
        tokens={"prompt": 12, "completion": 4},
    )
    assert m["tool_calls"] == [{"name": "web_search", "args": {"q": "x"}}]
    assert m["tokens"] == {"prompt": 12, "completion": 4}


def test_append_message_truncates_to_max(tmp_path: Path):
    """Adding more than MAX_MESSAGES_PER_SESSION messages drops the
    oldest non-system message.
    """
    mgr = SessionManager(tmp_path, max_messages=5)
    s = mgr.create()
    # Pin a system message; it must NOT be dropped.
    mgr.append_message(s.id, "system", "you are a helpful assistant")
    for i in range(10):
        mgr.append_message(s.id, "user", f"msg-{i}")
    s2 = mgr.get(s.id)
    assert len(s2.messages) <= 5
    # The system message is preserved.
    assert s2.messages[0]["role"] == "system"
    # At least one of the user messages is flagged truncated.
    assert any(m.get("truncated") for m in s2.messages)


# ---------- update / delete ----------


def test_update_title(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    s = mgr.create()
    s2 = mgr.update(s.id, title="renamed")
    assert s2.title == "renamed"
    assert mgr.get(s.id).title == "renamed"


def test_update_pinned_and_archived(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    s = mgr.create()
    mgr.update(s.id, pinned=True)
    assert mgr.get(s.id).pinned is True
    mgr.update(s.id, archived=True)
    assert mgr.get(s.id).archived is True
    mgr.update(s.id, tags=["debug", "plugins"])
    assert mgr.get(s.id).tags == ["debug", "plugins"]
    mgr.update(s.id, model="mock-default")
    assert mgr.get(s.id).model == "mock-default"


def test_update_missing(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    assert mgr.update("s_nope", title="x") is None


def test_soft_delete(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    s = mgr.create()
    assert mgr.soft_delete(s.id) is True
    assert mgr.get(s.id).archived is True
    # And it disappears from the default list.
    assert all(r["id"] != s.id for r in mgr.list_summaries())


def test_hard_delete(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    s = mgr.create()
    path = tmp_path / "sessions" / f"{s.id}.json"
    assert path.exists()
    assert mgr.hard_delete(s.id) is True
    assert not path.exists()
    assert mgr.get(s.id) is None
    assert mgr.hard_delete(s.id) is False


# ---------- list / search ----------


def test_list_summaries_default_excludes_archived(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    a = mgr.create(title="a")
    b = mgr.create(title="b")
    c = mgr.create(title="c")
    mgr.soft_delete(b.id)
    results = mgr.list_summaries()
    ids = [r["id"] for r in results]
    assert a.id in ids
    assert c.id in ids
    assert b.id not in ids
    # include_archived=True surfaces it.
    results_with = mgr.list_summaries(include_archived=True)
    assert b.id in [r["id"] for r in results_with]


def test_list_summaries_pinned_first(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    a = mgr.create(title="a")
    b = mgr.create(title="b")
    c = mgr.create(title="c")
    mgr.update(b.id, pinned=True)
    results = mgr.list_summaries()
    ids = [r["id"] for r in results]
    assert ids[0] == b.id  # pinned first
    # The other two follow in updated_at desc order.
    remaining = ids[1:]
    # Either a then c, or c then a — both have later updated_at.
    assert a.id in remaining
    assert c.id in remaining


def test_list_summaries_search_title_and_content(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    a = mgr.create(title="Plugin debugging")
    b = mgr.create(title="C8 webhook")
    c = mgr.create(title="Setup")
    mgr.append_message(b.id, "user", "tell me about the harness architecture")
    mgr.append_message(c.id, "user", "I need help with the plugin system")
    # Match by title (case-insensitive substring).
    matches = mgr.list_summaries(search="plugin")
    ids = [r["id"] for r in matches]
    assert a.id in ids  # title "Plugin debugging" contains "plugin"
    assert c.id in ids  # content contains "plugin"
    # Match by message content.
    matches = mgr.list_summaries(search="harness")
    ids = [r["id"] for r in matches]
    assert b.id in ids
    # Match across title + content.
    matches = mgr.list_summaries(search="system")
    ids = [r["id"] for r in matches]
    assert c.id in ids
    # No matches.
    assert mgr.list_summaries(search="nonexistent") == []


def test_list_summaries_limit(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    for i in range(10):
        mgr.create(title=f"s{i}")
    results = mgr.list_summaries(limit=3)
    assert len(results) == 3


# ---------- atomicity / persistence ----------


def test_atomic_write_no_partial_files(tmp_path: Path):
    """After a series of writes, no .tmp files should remain in
    the sessions directory.
    """
    mgr = SessionManager(tmp_path)
    s = mgr.create()
    for i in range(5):
        mgr.append_message(s.id, "user", f"m{i}")
    tmp_files = list((tmp_path / "sessions").glob("*.tmp"))
    assert tmp_files == []


def test_message_count_in_summary(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    s = mgr.create()
    for i in range(3):
        mgr.append_message(s.id, "user", f"m{i}")
    summary = mgr.list_summaries()[0]
    assert summary["message_count"] == 3


def test_summary_fields(tmp_path: Path):
    mgr = SessionManager(tmp_path)
    s = mgr.create()
    mgr.update(s.id, pinned=True, tags=["x"], model="m")
    summary = mgr.list_summaries()[0]
    assert summary["pinned"] is True
    assert summary["tags"] == ["x"]
    assert summary["model"] == "m"
    assert "created_at" in summary
    assert "updated_at" in summary


def test_search_by_title(tmp_path):
    mgr = SessionManager(tmp_path)
    s1 = mgr.create("Python Architecture")
    s2 = mgr.create("JavaScript Tips")
    results = mgr.search("python")
    assert len(results) == 1
    assert results[0].id == s1.id
    # the unrelated session must not surface
    assert s2.id not in {r.id for r in results}


def test_search_by_message_content(tmp_path):
    mgr = SessionManager(tmp_path)
    s = mgr.create("Daily standup")
    mgr.append_message(s.id, "user", "How does async/await work?")
    mgr.append_message(s.id, "assistant", "It schedules coroutines on the event loop.")
    results = mgr.search("async")
    assert len(results) == 1
    assert results[0].id == s.id
    # and a needle that matches neither title nor content returns nothing
    assert mgr.search("zzz-no-match") == []


def test_search_empty_query_returns_empty(tmp_path):
    mgr = SessionManager(tmp_path)
    mgr.create("Anything")
    assert mgr.search("") == []
    assert mgr.search("   ") == []
