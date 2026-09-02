"""dhc.services.session_manager: persistent chat session storage.

A `Session` is a single conversation between the user and the LLM.
It owns:
  - a stable `id` (auto-generated, format: `s_<16 hex>`)
  - a `title` (auto-generated from the first user message, or user-set)
  - the list of `messages` (user/assistant/tool turns, each with
    content + timestamp + optional tool_calls and token counts)
  - metadata: `tags`, `pinned`, `archived`, `model` (the LLM model
    name the user picked, e.g. "gpt-4o"), `created_at`, `updated_at`

Storage:

    ~/.dhc/sessions/{id}.json
    + a sidecar `~/.dhc/sessions/index.json` that maps `updated_at`
      order to session ids so the session list endpoint does not
      have to stat() every file every time.

Atomicity:

    Every write goes through `_atomic_write_json`, which writes to a
    `*.tmp` file and `os.replace`s it onto the target. On a crash
    mid-write, the original file is intact.

Size cap:

    A session may grow to at most `MAX_MESSAGES_PER_SESSION` (1000
    by default). When a session is full, the OLDEST message is
    dropped (a `truncated: true` flag is set on the message that
    took its place). This is documented in `docs/session-storage.md`.

Concurrency:

    The manager holds a single re-entrant lock for all mutations.
    Reads are lock-free and use a small LRU.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any


MAX_MESSAGES_PER_SESSION = 1000
TITLE_WORD_COUNT = 8
TITLE_MAX_CHARS = 60

VALID_ROLES = frozenset({"user", "assistant", "system", "tool"})

_ID_ALPHABET = "0123456789abcdef"


def _new_id() -> str:
    return "s_" + "".join(secrets.choice(_ID_ALPHABET) for _ in range(16))


def _autotitle(text: str) -> str:
    """Generate a short, human-readable session title from the first
    user message. Strips newlines, collapses whitespace, takes the
    first 8 words or 60 characters, whichever is shorter.
    """
    if not text:
        return "New session"
    flat = re.sub(r"\s+", " ", text).strip()
    words = flat.split(" ")
    if len(words) > TITLE_WORD_COUNT:
        flat = " ".join(words[:TITLE_WORD_COUNT]).rstrip(",.;:")
    if len(flat) > TITLE_MAX_CHARS:
        flat = flat[:TITLE_MAX_CHARS].rsplit(" ", 1)[0] or flat[:TITLE_MAX_CHARS]
    return flat or "New session"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write a JSON file: write to `*.tmp`, then rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


class Session:
    def __init__(
        self,
        id: str,
        title: str,
        created_at: int,
        updated_at: int,
        messages: list[dict[str, Any]],
        model: str = "",
        tags: list[str] | None = None,
        pinned: bool = False,
        archived: bool = False,
    ) -> None:
        self.id = id
        self.title = title
        self.created_at = created_at
        self.updated_at = updated_at
        self.messages = messages
        self.model = model
        self.tags = list(tags) if tags else []
        self.pinned = pinned
        self.archived = archived

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": self.messages,
            "model": self.model,
            "tags": self.tags,
            "pinned": self.pinned,
            "archived": self.archived,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Session":
        return cls(
            id=str(d["id"]),
            title=str(d.get("title") or "New session"),
            created_at=int(d.get("created_at") or 0),
            updated_at=int(d.get("updated_at") or 0),
            messages=list(d.get("messages") or []),
            model=str(d.get("model") or ""),
            tags=list(d.get("tags") or []),
            pinned=bool(d.get("pinned") or False),
            archived=bool(d.get("archived") or False),
        )

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": len(self.messages),
            "model": self.model,
            "tags": self.tags,
            "pinned": self.pinned,
            "archived": self.archived,
        }


class SessionManager:
    """Persistent chat-session storage with atomic writes.

    The constructor takes a directory; the manager creates the
    `sessions/` and `index.json` files lazily on first use.
    """

    def __init__(self, root_dir: Path, max_messages: int = MAX_MESSAGES_PER_SESSION) -> None:
        self._root = Path(root_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        self._dir = self._root / "sessions"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._root / "sessions-index.json"
        self._max_messages = max_messages
        self._lock = threading.RLock()
        # In-memory cache: id -> Session. Populated on first read.
        self._cache: dict[str, Session] = {}

    # ----- create / read / update / delete -----

    def create(self, title: str | None = None) -> Session:
        with self._lock:
            now = int(time.time() * 1000)
            s = Session(
                id=_new_id(),
                title=title or "New session",
                created_at=now,
                updated_at=now,
                messages=[],
            )
            self._save(s)
            self._cache[s.id] = s
            return s

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            if session_id in self._cache:
                return self._cache[session_id]
            path = self._dir / f"{session_id}.json"
            if not path.exists():
                return None
            s = Session.from_dict(json.loads(path.read_text(encoding="utf-8")))
            self._cache[session_id] = s
            return s

    def list_summaries(
        self,
        include_archived: bool = False,
        search: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """List session summaries sorted by `pinned desc, updated_at desc`.

        `search` matches if it appears in the title or any message
        content (case-insensitive). `limit` caps the number of
        summaries returned (no effect on side effects).
        """
        with self._lock:
            self._ensure_index()
            index = self._load_index()
            results: list[dict[str, Any]] = []
            needle = (search or "").strip().lower()
            for sid in index.get("ids", []):
                s = self.get(sid)
                if s is None:
                    continue
                if not include_archived and s.archived:
                    continue
                if needle:
                    hay = s.title.lower() + "\n" + "\n".join(
                        str(m.get("content") or "") for m in s.messages
                    )
                    if needle not in hay.lower():
                        continue
                results.append(s.summary())
                if limit is not None and len(results) >= limit:
                    break
            # Sort: pinned first, then by updated_at desc.
            results.sort(key=lambda r: (not r.get("pinned", False), -r["updated_at"]))
            return results

    def search(
        self,
        query: str,
        limit: int = 50,
        include_archived: bool = False,
    ) -> list[Session]:
        """Case-insensitive search across session title and message content.

        Returns full ``Session`` objects (not summaries) ordered by
        ``updated_at`` desc, with pinned sessions floated to the top.
        Archived sessions are excluded unless ``include_archived`` is
        true. An empty/whitespace query returns an empty list; callers
        should fall back to ``list_summaries`` for the full listing.
        """
        with self._lock:
            self._ensure_index()
            index = self._load_index()
            needle = (query or "").strip().lower()
            if not needle:
                return []
            matched: list[Session] = []
            for sid in index.get("ids", []):
                if len(matched) >= limit:
                    break
                s = self.get(sid)
                if s is None:
                    continue
                if not include_archived and s.archived:
                    continue
                hay = s.title.lower() + "\n" + "\n".join(
                    str(m.get("content") or "") for m in s.messages
                )
                if needle in hay:
                    matched.append(s)
            matched.sort(
                key=lambda s: (not s.pinned, -s.updated_at)
            )
            return matched

    def update(
        self,
        session_id: str,
        title: str | None = None,
        pinned: bool | None = None,
        archived: bool | None = None,
        tags: list[str] | None = None,
        model: str | None = None,
    ) -> Session | None:
        with self._lock:
            s = self.get(session_id)
            if s is None:
                return None
            if title is not None:
                s.title = title
            if pinned is not None:
                s.pinned = bool(pinned)
            if archived is not None:
                s.archived = bool(archived)
            if tags is not None:
                s.tags = list(tags)
            if model is not None:
                s.model = model
            s.updated_at = int(time.time() * 1000)
            self._save(s)
            return s

    def soft_delete(self, session_id: str) -> bool:
        return self.update(session_id, archived=True) is not None

    def hard_delete(self, session_id: str) -> bool:
        with self._lock:
            s = self.get(session_id)
            if s is None:
                return False
            path = self._dir / f"{session_id}.json"
            if path.exists():
                path.unlink()
            self._cache.pop(session_id, None)
            self._ensure_index()
            index = self._load_index()
            index["ids"] = [i for i in index.get("ids", []) if i != session_id]
            _atomic_write_json(self._index_path, index)
            return True

    # ----- message operations -----

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        tokens: dict[str, int] | None = None,
        auto_title: bool = True,
    ) -> dict[str, Any] | None:
        if role not in VALID_ROLES:
            raise ValueError(f"invalid role {role!r}; must be one of {sorted(VALID_ROLES)}")
        with self._lock:
            s = self.get(session_id)
            if s is None:
                return None
            now = int(time.time() * 1000)
            message_id = "m_" + "".join(secrets.choice(_ID_ALPHABET) for _ in range(12))
            msg = {
                "id": message_id,
                "role": role,
                "content": content,
                "ts_ms": now,
            }
            if tool_calls:
                msg["tool_calls"] = tool_calls
            if tokens:
                msg["tokens"] = tokens
            s.messages.append(msg)
            # Cap: drop oldest non-system message if we exceed.
            truncated = False
            while len(s.messages) > self._max_messages:
                # find first non-system message
                idx = next(
                    (i for i, m in enumerate(s.messages) if m.get("role") != "system"),
                    None,
                )
                if idx is None:
                    break
                s.messages.pop(idx)
                truncated = True
            if truncated:
                # The first non-system message gets the flag.
                for m in s.messages:
                    if m.get("role") != "system":
                        m["truncated"] = True
                        break
            # Auto-title from the first user message.
            if auto_title and s.title in ("", "New session") and role == "user" and content:
                s.title = _autotitle(content)
            s.updated_at = now
            self._save(s)
            return msg

    # ----- internals -----

    def _save(self, s: Session) -> None:
        path = self._dir / f"{s.id}.json"
        _atomic_write_json(path, s.to_dict())
        # Update the index.
        self._ensure_index()
        index = self._load_index()
        ids = list(index.get("ids", []))
        if s.id not in ids:
            ids.append(s.id)
        index["ids"] = ids
        _atomic_write_json(self._index_path, index)

    def _load_index(self) -> dict[str, Any]:
        if not self._index_path.exists():
            return {"ids": []}
        return json.loads(self._index_path.read_text(encoding="utf-8"))

    def _ensure_index(self) -> None:
        """Rebuild the index from disk if it's missing or stale.

        Stale means: an id appears in the index but the file is gone,
        or a session file exists on disk but is not in the index.
        """
        index = self._load_index()
        indexed = set(index.get("ids", []))
        on_disk = {p.stem for p in self._dir.glob("*.json") if not p.name.endswith(".tmp")}
        # Always rebuild from disk to be consistent.
        if indexed != on_disk:
            new_ids = sorted(on_disk)
            _atomic_write_json(self._index_path, {"ids": new_ids})


__all__ = [
    "MAX_MESSAGES_PER_SESSION",
    "Session",
    "SessionManager",
    "TITLE_MAX_CHARS",
    "TITLE_WORD_COUNT",
]
