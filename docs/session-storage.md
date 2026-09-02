# Session storage (v1.2.0)

Chat sessions are persisted to disk by the
`dhc.services.session_manager.SessionManager` service. This
document describes the on-disk layout, the write path, the
concurrency model, and the size caps. It complements
`chat-architecture.md` and the API contracts in
`docs/README.md`.

## On-disk layout

```
<root_dir>/
├── sessions/
│   ├── s_<id>.json
│   └── s_<id>.json.tmp   (only during atomic write)
└── sessions-index.json   (lightweight index of all session ids)
```

`<root_dir>` defaults to `~/.dhc/` (so `<root_dir>/sessions/` is
`~/.dhc/sessions/`). The harness `serve_c1` entry point accepts
`--sessions-dir` to override this.

Each `s_<id>.json` contains a single JSON document:

```json
{
  "id": "s_3775f31e708cb7ac",
  "title": "Plugin debugging",
  "created_at": 1735920000000,
  "updated_at": 1735920012345,
  "messages": [
    {
      "id": "m_abc123",
      "role": "user",
      "content": "How do I install the dhc plugin?",
      "ts_ms": 1735920010000
    },
    {
      "id": "m_def456",
      "role": "assistant",
      "content": "Use the marketplace panel...",
      "ts_ms": 1735920012345,
      "tokens": {"prompt": 12, "completion": 88}
    }
  ],
  "model": "mock-default",
  "tags": ["debug"],
  "pinned": false,
  "archived": false
}
```

The `sessions-index.json` file is a flat array of session ids:

```json
{
  "ids": ["s_3775f31e708cb7ac", "s_9a8b7c6d5e4f3a2b"]
}
```

The index is rebuilt from disk on every read if it is stale
(missing ids, or filesystem has files the index does not list).

## Atomic writes

Every write goes through `_atomic_write_json`:

```python
def _atomic_write_json(path, payload):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ...), encoding="utf-8")
    os.replace(tmp, path)
```

The `os.replace` is atomic on POSIX and on Windows (when the
target is on the same volume, which it always is here). On a
crash mid-write, the original file is intact; the next read will
see the previous version, not a half-written one.

The repository's `tests/chat/test_session_manager.py` asserts that
no `*.tmp` files remain in `sessions/` after a series of writes.

## Concurrency

`SessionManager` holds a single re-entrant lock (`threading.RLock`)
for all mutations. Reads are lock-free; they use a small
in-memory cache populated on first access.

The unit test `test_service_concurrent_puts` exercises 20 puts
from each of two threads and asserts no errors.

## Size cap

Each session may grow to at most `MAX_MESSAGES_PER_SESSION` (1000
by default). When a session is full, the OLDEST non-system
message is dropped. The first non-system message in the session
is marked `truncated: true` so the UI can show a "truncated"
indicator.

System messages are preserved; they are part of the LLM context
and not the user/assistant log.

## Truncation invariants

- A system message is never dropped, even when the cap is hit.
- The cap is applied per-session; the manager does not enforce a
  global session count limit. v1.2.0 does not need one (the mock
  LLM is offline).
- The `truncated` flag is a hint for the UI; the server does not
  interpret it.

## Soft delete vs hard delete

`DELETE /api/sessions/{id}` defaults to soft delete: the session
is marked `archived: true` and excluded from the default
`GET /api/sessions` list. The file remains on disk.

`DELETE /api/sessions/{id}?hard=1` removes the file and the id
from the index. The session cannot be recovered.

Soft delete is idempotent (deleting an already-archived session
returns `204` with no effect). Hard delete returns `204` on
success and `404` if the session does not exist.

## Search

`GET /api/sessions?search=...` does a case-insensitive substring
match against the title AND against every message's content.
The search is O(N) over all sessions; for v1.2.0 this is fine
because the total session count is bounded by the user. A future
v1.3.0 can add a full-text index if needed.

The search query is HTML-escaped by the React client before being
placed in the URL, so the search input is XSS-safe (the server
treats it as a plain string).

## Recency grouping

The React `SessionList` component groups sessions into Today /
Yesterday / Older based on `updated_at` and the current time. This
is purely a UI concern; the server returns the list sorted by
`(pinned desc, updated_at desc)`.

## What is NOT stored

- The bearer token (it lives only in the C1 process memory and the
  served `index.html`)
- The LLM API key (lives in the encrypted secrets store when v1.3.0
  lands; see `secrets-model.md`)
- The stream of intermediate LLM deltas (the session log stores
  only the final concatenated assistant turn)

## Backup / restore

The on-disk format is a flat directory of JSON files. A backup is
just `tar`/`zip` of the `sessions/` directory. To restore, copy
the files back; the next read will re-hydrate the index.
