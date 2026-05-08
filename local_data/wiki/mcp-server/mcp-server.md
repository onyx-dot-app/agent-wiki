# MCP Server (inbound)

How the wiki exposes itself **as** an MCP server so external coding agents
(Claude Code, Cursor, Codex, custom harnesses) can search, read, edit, and
subscribe to docs. This is the **inbound** surface — distinct from the
outbound MCP-client surface (`backend/app/api/mcp.py` →
`mcp_connections.py` after the rename below) which lets *our* agent harness
consume *other* MCP servers.

Source of truth for the editing primitives the MCP wraps:
[tool-design](../tool-design/tool-design.md). This doc covers the
transport, auth, resource subscription, staleness model, and the tools
that exist only on the MCP surface (`update_doc_nl`, `ask_nl_question`,
`apply_patch`, `read_doc` with `sha`, `list_history`).

## Goal

Let an external agent treat the wiki as a first-class workspace:

1. **Discover** — `search_wiki`, `list_history`, `ask_nl_question`.
2. **Read** — `read_doc(path, sha?)`, with auto-subscription so the agent
   gets pushed an update the moment the doc changes underneath it.
3. **Write** — `edit_doc` (find-and-replace), `apply_patch` (line-anchored
   unified diff), `multi_edit`, `write_doc`, `move_doc`, `create_directory`,
   plus the heavier `update_doc_nl` (NL instruction → document-updater
   agent → commit).
4. **Stay in sync** — MCP `resources/subscribe` on `wiki:///<path>`. On
   commit (from any source — human, another agent, the doc-updater),
   subscribers receive `notifications/resources/updated` over their open
   SSE stream.

Non-goals for v0: outbound dispatch from triggers via MCP, streaming
partial tool results, multi-tenant isolation beyond user-scoped tokens,
persistent subscriptions across reconnects.

## Surface — transport and framing

### Streamable HTTP

Use MCP's **Streamable HTTP** transport (the modern replacement for
HTTP+SSE). One endpoint, two methods:

- `POST /api/mcp` — JSON-RPC 2.0 request/notification. Response is either
  a single JSON-RPC reply or an `text/event-stream` upgrade if the server
  needs to stream multi-step output.
- `GET /api/mcp` — opens a long-lived SSE stream for **server-initiated**
  messages (resource update notifications, list-changed notifications,
  progress events for long-running tools).

Session id is established on the first `initialize` call and carried in
the `Mcp-Session-Id` header on every subsequent request. The server keeps
session state (subscriptions, seen_paths, idempotency keys) keyed by that
id, in-process for v0.

### JSON-RPC framing

Use the official `mcp` Python SDK (`pip install mcp`) for the framing,
capability negotiation, and SSE plumbing. Hand-rolling JSON-RPC for this
gains nothing. The SDK plugs into ASGI, so we mount it on the Flask app
via `a2wsgi` or run it under the same uvicorn process Flask uses for SSE
chat (the chat agent already streams over SSE — same pattern).

If the SDK proves heavy or surprising, the fallback is to write a thin
JSON-RPC handler in `app/mcp_server/transport.py` and reuse the SSE
plumbing from `app/api/chat.py`. Decision deferred to implementation, but
default to **SDK first**.

### Auth — per-user personal API token

Bearer auth on the HTTP transport. Header: `Authorization: Bearer
mcp_<token>`. Token is hashed-at-rest (`bcrypt` or `hashlib.sha256` —
match what `app/auth/users.py` uses for passwords) and resolves to a
`user_id`. Every commit made through this session uses that user's git
identity (`name <email>`).

New table (migration 0007):

```sql
CREATE TABLE mcp_tokens (
  id          INTEGER PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name        TEXT    NOT NULL,        -- human label, e.g. "claude-code laptop"
  token_hash  TEXT    NOT NULL UNIQUE, -- sha256 of the raw token
  created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
  last_used_at TEXT
);
CREATE INDEX idx_mcp_tokens_user ON mcp_tokens(user_id);
```

The raw token is shown **once** at creation (`mcp_<32 hex chars>` so it's
distinguishable from session cookies in logs) and never stored. Lost
tokens get revoked + reissued.

New repo: `app/auth/mcp_tokens.py` — `create(user_id, name) -> (token_id,
raw_token)`, `verify(raw_token) -> User | None`, `list(user_id)`,
`revoke(token_id, user_id)`.

New API (admin-free, user-scoped) under `app/api/mcp_tokens.py`:
- `GET /api/mcp/tokens` — list current user's tokens (no hashes).
- `POST /api/mcp/tokens {name}` — mint, return raw token in body once.
- `DELETE /api/mcp/tokens/<id>` — revoke.

Frontend page: `frontend/src/app/settings/mcp-tokens/page.tsx` — list +
"New token" button + reveal-once modal. Reuses `apiFetch` and
`useRequireAuth`.

## Tool surface

The MCP tool registry **shares** the agent-tool registry per
[seams.md](../seams.md). Tools the in-process chat agent already exposes
(`search_wiki`, `read_page`, `edit_doc`, `multi_edit`, `write_doc`,
`move_path`, `create_directory`, `create_trigger`, `update_trigger`) get
re-exposed as MCP tools by walking `app/llm/agents/tools/__init__.py:REGISTRY`.

Tools that exist **only** on the MCP surface (because the in-process
chat agent has direct loop access and doesn't need them) live in a
sibling registry `app/mcp_server/tools/` and are merged at server-start
into the exported tool list.

### Inventory

| Tool | Source | Notes |
| --- | --- | --- |
| `search_wiki` | shared | Returns BM25 snippets. Auto-populates session `seen_paths` is **not** done here — search-only sessions still need explicit `read_doc` before edits, same rule as the chat agent. |
| `read_doc(path, sha?, subscribe=true)` | mcp-only (replaces `read_page`) | `sha` defaults to HEAD. Returns `{path, body, sha, is_head}`. If `subscribe=true` (default), session subscribes to `wiki:///<path>` so future commits push notifications. Adds `path` to `seen_paths` only when `is_head` (a historical read doesn't authorize an edit). |
| `list_history(path, limit=20)` | mcp-only | Wraps `wiki_git.history(rel)`. Returns `[{sha, author, ts, message, deprecated_by}, ...]` — same shape as the existing `GET /api/documents/file/history`. |
| `edit_doc(path, edits[], message, base_sha?)` | shared (`multi_edit` shape) | Atomic batch of `{old_string, new_string, replace_all?}`. `base_sha` is new — see "Concurrency". |
| `apply_patch(path, patch, message, base_sha?)` | mcp-only | Unified diff with `@@ -L,N +L,M @@` hunks. See "Patch tool" below. |
| `write_doc(path, body, message, base_sha?)` | shared | Full-body create or overwrite. `base_sha` required when overwriting an existing file. |
| `move_doc(old_path, new_path, message)` | shared (`move_path`) | Rename. |
| `create_directory(path, message)` | shared | Empty folder via `.gitkeep`. |
| `update_doc_nl(path, instruction, idempotency_key?, base_sha?)` | mcp-only | NL update — see "NL update tool" below. |
| `ask_nl_question(query)` | mcp-only | NL Q&A over the wiki — see "NL question tool" below. |
| `create_trigger`, `update_trigger` | shared | Same shape as in-process. |

### `read_doc` with `sha`

```
read_doc(path: str, sha: str | None = None, subscribe: bool = True)
  -> {path, body, sha, is_head: bool}
```

- `sha` omitted → reads HEAD via `wiki_git.read_file(rel)`.
- `sha` provided → reads via `wiki_git.read_file_at_ref(rel, sha)` (new
  helper — `git show <sha>:<rel>`). Errors with `{"error": "sha_not_found"}`
  if the sha doesn't exist or the file wasn't tracked at that sha.
- Auto-subscribe: when `subscribe=true` and `is_head`, the session is
  registered as a subscriber for `wiki:///<path>`. Auto-subscribe is
  intentionally HEAD-only — subscribing to a historical sha is meaningless.
- `seen_paths` is populated only on a HEAD read so an agent can't read
  an old version and then edit blindly against HEAD.

### `apply_patch` (line-anchored unified diff)

We deliberately skipped this in `tool-design.md` for the in-process
agent because the model produces unified-diff format unreliably without
strong training signal, and the fuzzy `edit_doc` chain covers most cases.
**On the MCP surface we add it**, because external agents (Claude Code,
Codex) often have line-numbered context and expect to send `+/-` diffs.

```
apply_patch(path: str, patch: str, message: str, base_sha: str | None = None)
  -> {path, sha, diff, applied_hunks, broken_links?}
```

`patch` is a standard unified diff body (no `*** Begin Patch` envelope —
plain `--- a/x`, `+++ b/x`, then `@@` hunks). Implementation in
`app/wiki/patch.py`:

1. Parse hunks (one of `whatthepatch`, `unidiff`, or hand-rolled — check
   if any are already in `requirements.txt`; default to a small
   hand-rolled parser).
2. For each hunk:
   - **Try at the specified line range first.** If the context lines
     (`' '` lines) match exactly at the offset, apply.
   - **Fallback: locate by content.** Drop the line numbers, build the
     "before" text from context + `-` lines, run it through
     `app/wiki/edit.py:replace()`. If it matches uniquely, apply.
   - **Fail.** Return `{"error": "hunk_failed", "hunk_index": i,
     "expected": "...", "found": "..."}`. The whole patch aborts (atomic).
3. On success, commit + reindex + fan-out via `commit_and_fan_out`.

This gives us the line-number ergonomics agents expect, with the same
fuzzy safety net `edit_doc` already has.

### `update_doc_nl` (NL instruction → document-updater)

```
update_doc_nl(path: str, instruction: str, idempotency_key: str | None = None,
              base_sha: str | None = None) -> {job_id, status_uri}
```

This is the "I have now completed the TODO under section X which is
blah blah" entry point. It's heavy (LLM call) and async.

Flow:

1. Validate path + check `seen_paths` (file must exist; agent must have
   `read_doc`'d it).
2. Compute `idempotency_key` if not provided: `sha256(user_id + path +
   instruction)`. Look up an existing pending/completed job with that
   key; if found, return its `job_id` (no new enqueue).
3. Insert `mcp_jobs` row with `status='pending'`, `kind='update_doc_nl'`,
   `payload_json={path, instruction, base_sha}`.
4. Enqueue `tasks.document_update.update_document_direct(job_id)` on
   `documents_huey` (see [background-tasks](../background-tasks/background-tasks.md)).
5. Return `{job_id, status_uri: "job://<job_id>"}`. The agent can
   `resources/subscribe` to the URI to be pushed completion.

Worker side (`tasks/document_update.py`):

1. Load job row + current body + `base_sha`.
2. If `base_sha` set and HEAD differs, mark job `failed` with
   `error="stale_base"` and the diff since base. Publish job update.
3. Run `app.llm.agents.document_updater.run(doc_id, current_body,
   {instruction}, source="mcp")`.
4. On `NO_CHANGE`: mark job `succeeded` with `committed=false`. Publish.
5. On new body: `commit_and_fan_out` → mark job `succeeded` with
   `committed=true, sha=<new>`. Publish.
6. On exception: mark job `failed` with `error=<code>` from `LLMError`.

New table (migration 0008):

```sql
CREATE TABLE mcp_jobs (
  id              TEXT PRIMARY KEY,           -- ulid
  user_id         INTEGER NOT NULL REFERENCES users(id),
  kind            TEXT NOT NULL,              -- 'update_doc_nl' for now
  status          TEXT NOT NULL,              -- pending|running|succeeded|failed
  idempotency_key TEXT,
  payload_json    TEXT NOT NULL,
  result_json     TEXT,
  error           TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  finished_at     TEXT
);
CREATE UNIQUE INDEX idx_mcp_jobs_idemp
  ON mcp_jobs(user_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
```

Frequency hint (server-side debounce): inside the worker, before invoking
the LLM, check if a `succeeded` job for the same `(user_id, path)`
committed within the last 30 seconds. If so, mark this one
`succeeded committed=false reason=debounced`. Cheap insurance against a
chatty agent. The 30s window is configurable (`MCP_NL_DEBOUNCE_SECONDS`).

### `ask_nl_question` (NL Q&A over the wiki)

```
ask_nl_question(query: str, max_sources: int = 8)
  -> {answer, sources: [{path, sha}]}
```

Read-only RAG over the wiki. Sync (block until done) for v0 — most
agents tolerate 30–60s tool calls. If we see chronic timeouts, switch to
the same job pattern as `update_doc_nl`.

Implementation: dispatches to a one-shot variant of the chat-agent
harness with:
- `search_wiki` + `read_page` enabled
- All write tools disabled
- Max 6 tool-call iterations (hard cap)
- System prompt instructs: "Answer concisely with citations to wiki
  paths. Do not propose edits."

Lives in `app/llm/agents/wiki_qa.py:run(query) -> {answer, sources}`.
The MCP tool wrapper just calls it. The chat-agent loop primitive
(`app/llm/agents/loop.py`) is reused — we just instantiate it with a
narrower toolset and write-disabled mode.

## Resources — subscription model

MCP resources expose URI-addressable content the client can list, read,
and subscribe to. The server pushes `notifications/resources/updated`
when the content changes.

### URI scheme

- `wiki:///<rel-path>` — a markdown doc. Body is `text/markdown`.
- `wiki:///` — the wiki root (returns the tree as JSON, `application/json`).
- `job://<job_id>` — async job status. Body is JSON `{status, result, error}`.

`resources/list` returns:

- One entry per `.md` file under `<wiki>/` (walked once at session start,
  cached, refreshed on `notifications/resources/list_changed`).
- One entry per active `mcp_jobs` row owned by the session's user.

`resources/read` dispatches:

- `wiki:///<path>` → `wiki_git.read_file(rel)` (HEAD only — historical
  reads go through `read_doc(path, sha)`).
- `wiki:///` → tree walk via `app/wiki/filesystem.py:walk_tree()`.
- `job://<id>` → query `mcp_jobs` row.

`resources/subscribe`:

- `wiki:///<path>` → record `(session_id, path)` in the subscription
  registry.
- `job://<id>` → record `(session_id, job_id)`.

`resources/unsubscribe` removes the entry. Subscriptions die with the
session.

### Pub-sub — hooked into `commit_and_fan_out`

The single seam for **all** wiki commits is
`app/llm/agents/tools/_doc_helpers.py:commit_and_fan_out`. Today it does
two side-effects post-commit: `reindex_path` and `fan_out_trigger_eval`.
We add a third:

```python
def commit_and_fan_out(rel, body, message, *, change_kind):
    ...
    sha = wiki_git.commit_file(rel, body, message, author=author)
    reindex_path(rel)
    fan_out_trigger_eval(rel, sha, change_kind, author)
    mcp_pubsub.publish_doc_update(rel, sha, change_kind)   # NEW
    return sha
```

`mcp_pubsub` is `app/mcp_server/pubsub.py` — a tiny in-process pub-sub:

```python
# app/mcp_server/pubsub.py
class PubSub:
    def subscribe_doc(self, session_id: str, rel: str) -> None: ...
    def unsubscribe_doc(self, session_id: str, rel: str) -> None: ...
    def subscribe_job(self, session_id: str, job_id: str) -> None: ...
    def publish_doc_update(self, rel: str, sha: str, kind: str) -> None: ...
    def publish_job_update(self, job_id: str, status: str) -> None: ...
    def drain(self, session_id: str) -> list[Notification]: ...
    def forget(self, session_id: str) -> None: ...

PUBSUB = PubSub()
```

`publish_doc_update` looks up subscribers for `rel`, enqueues a
`Notification` per session into a per-session `asyncio.Queue` (or
`queue.Queue` with a futures bridge — depends on whether we end up under
asyncio for the SDK). The transport layer's SSE writer awaits the queue
and serializes each notification as a JSON-RPC message.

Cross-process complication: Huey workers run in a different process than
the Flask/MCP server. The doc-updater agent commits from the worker. The
worker's `commit_and_fan_out` therefore needs to reach into the
**server-process** subscription registry. Two options:

- **(v0) SQLite-backed pub-sub.** `mcp_subscriptions` table for active
  subscriptions; `mcp_notifications` table as a queue. Worker INSERTs a
  notification row tagged with the session ids. Server polls (every
  100ms) or listens via SQLite's `update_hook` C-API (cleaner but
  pythonic-bindings-shaky). Polling is fine for v0 — wiki commits are
  not high-frequency.
- **(later) Redis pub-sub or NATS.** Defer until SQLite polling is a
  bottleneck. Note this in seams.md.

Default to **SQLite-backed pub-sub** for v0:

```sql
-- migration 0009
CREATE TABLE mcp_subscriptions (
  session_id  TEXT NOT NULL,
  uri         TEXT NOT NULL,             -- 'wiki:///foo.md' or 'job://abc'
  user_id     INTEGER NOT NULL REFERENCES users(id),
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (session_id, uri)
);
CREATE INDEX idx_mcp_subs_uri ON mcp_subscriptions(uri);

CREATE TABLE mcp_notifications (
  id          INTEGER PRIMARY KEY,
  session_id  TEXT NOT NULL,
  uri         TEXT NOT NULL,
  payload_json TEXT NOT NULL,            -- {sha, kind} or {status, ...}
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  delivered_at TEXT
);
CREATE INDEX idx_mcp_notifs_undelivered
  ON mcp_notifications(session_id) WHERE delivered_at IS NULL;
```

Server-side: a per-session task `tail_notifications(session_id)` polls
`mcp_notifications WHERE session_id=? AND delivered_at IS NULL ORDER BY
id` every 100ms, ships matches over the SSE stream, and stamps
`delivered_at`. Sessions clean up their rows on disconnect (or a 24h
janitor task).

## Concurrency — the staleness contract

This is the "doc changed under the agent" guarantee. Three layers,
strongest to weakest:

### 1. Optimistic concurrency (`base_sha`) — hard correctness

Every write tool accepts `base_sha`. If set and `wiki_git.head_sha_for_path(rel)
!= base_sha`:

```json
{
  "error": "stale_base",
  "base_sha": "<what agent sent>",
  "current_sha": "<head>",
  "diff_since_base": "<unified diff>"
}
```

Agent rebases (re-reads, re-edits) and retries.

`base_sha` semantics:

| Tool | base_sha behavior |
| --- | --- |
| `edit_doc` | Optional. If set, must match HEAD. Without it, the fuzzy `old_string` chain is the only safety. |
| `apply_patch` | Optional. Same as edit_doc. |
| `write_doc` | **Required when overwriting an existing file.** Without it, return `{"error": "base_sha_required_for_overwrite"}`. New-file creates skip the check. |
| `update_doc_nl` | Optional. Recorded on the job row; checked at task-execution time inside the worker (HEAD might have moved between enqueue and run). |
| `move_doc` | N/A — content unchanged. |

`read_doc` returns `sha` so the agent has it. Agent flow: `read_doc →
edit_doc(base_sha=<that sha>)`.

### 2. Push notification (`notifications/resources/updated`) — low latency

Every `read_doc` auto-subscribes the session to `wiki:///<path>`. On any
commit to that path (from anyone), the server fires
`notifications/resources/updated` over the session's SSE stream within
~100ms (limited by the SQLite poll interval; sub-millisecond once we
move to in-process pub-sub for same-process commits).

Well-behaved agents process the notification by re-reading before their
next edit. The notification carries the new `sha` so the agent knows
exactly what to fetch.

### 3. `stale_paths` field on tool results — belt-and-suspenders

Even agents that ignore notifications get a heads-up. Every tool result
on the MCP surface includes:

```json
{
  ...,
  "stale_paths": ["foo/bar.md", "baz.md"]
}
```

…listing every subscribed path that has changed since the session's
last tool call. Computed by reading the session's pending notifications
non-destructively (the SSE writer still ships them for completeness;
this field just surfaces the same info in-band).

### Combined guarantee

- `base_sha` is the only **hard** guarantee. Skipping it allows races.
  System-prompt language for the MCP tool descriptions strongly recommends
  it for every write.
- The push notification + `stale_paths` field together give the agent
  fast feedback so it doesn't *want* to skip `base_sha`.
- The fuzzy `edit_doc` chain is a final safety net — drift in `old_string`
  fails closed.

## Module layout

```
backend/app/mcp_server/
  __init__.py        # MCP server bootstrap; mounts JSON-RPC dispatch on Flask
  auth.py            # bearer token verify -> user
  session.py         # Session class: id, user_id, seen_paths, subs
  tools/
    __init__.py      # registry merge: agent-tool registry + mcp-only tools
    read_doc.py      # path + sha + auto-subscribe
    apply_patch.py   # unified diff
    update_doc_nl.py # async dispatch to document_updater
    ask_nl_question.py
    list_history.py
    move_doc.py      # thin wrapper of move_path
  resources.py       # list/read/subscribe handlers for wiki:// + job://
  pubsub.py          # SQLite-backed subscription registry + notification queue
  transport.py       # SSE writer per session (or SDK adapter)
  jobs.py            # mcp_jobs repo

backend/app/api/
  mcp_connections.py # RENAMED from mcp.py; outbound MCP-client management
  mcp_tokens.py      # NEW; user-scoped token CRUD
  mcp_server.py      # NEW; mounts /api/mcp Streamable HTTP endpoint

backend/app/wiki/
  patch.py           # NEW; parse + apply unified-diff hunks. Pure logic.
  git.py             # extend: read_file_at_ref(rel, sha), head_sha_for_path
                     # (already exists per tool-design.md history flow)

backend/app/auth/
  mcp_tokens.py      # NEW; bcrypt/sha256 hashing + verify

backend/app/db/migrations/
  0007_mcp_tokens.sql
  0008_mcp_jobs.sql
  0009_mcp_subscriptions.sql

backend/app/llm/agents/
  wiki_qa.py         # NEW; one-shot RAG harness for ask_nl_question
```

## Implementation plan — phased

### Phase 1 — auth and token surface (foundation)

1. Migration `0007_mcp_tokens.sql`.
2. `app/auth/mcp_tokens.py` repo (`create`, `verify`, `list`, `revoke`).
3. `app/api/mcp_tokens.py` blueprint + register in `app/main.py`.
4. Frontend `settings/mcp-tokens` page.
5. Tests: token round-trip, hash collision, revocation.

Exit criteria: a user can mint a token in the UI, see it once, and that
token's hash lands in the DB.

### Phase 2 — Streamable HTTP scaffold

1. Pull in the `mcp` Python SDK; smoke-test mounting it on the Flask
   app via ASGI bridge.
2. `app/mcp_server/auth.py` — bearer middleware that resolves token →
   user → `Session`.
3. `app/mcp_server/session.py` — Session object (id, user, seen_paths,
   created_at). In-memory dict keyed by `Mcp-Session-Id`.
4. `initialize` handshake: capability advertisement (`tools`,
   `resources`, `resources.subscribe`).
5. `app/api/mcp_server.py` blueprint mounted at `/api/mcp`.
6. Tests: `initialize` round-trip with a token; auth failure returns
   401; missing session id returns the protocol error.

Exit criteria: a Python MCP client can `initialize` against the running
server and see an empty tool list.

### Phase 3 — read-only tool surface

1. `app/mcp_server/tools/read_doc.py` — without `subscribe` yet, just
   `path + sha`.
2. `app/wiki/git.py:read_file_at_ref(rel, sha)` (uses `git show
   <sha>:<rel>`).
3. Re-export `search_wiki` from the agent-tool registry through the MCP
   tool list.
4. `app/mcp_server/tools/list_history.py` wrapping `wiki_git.history`.
5. `app/llm/agents/wiki_qa.py` + `ask_nl_question` MCP tool.
6. Tests: read HEAD, read at sha, read non-existent sha (error), search,
   history, ask.

Exit criteria: an agent can do discovery + reads end-to-end, including
historical reads.

### Phase 4 — write surface (sync edits)

1. Re-export `edit_doc`, `multi_edit`, `write_doc`, `move_path`,
   `create_directory` from the agent-tool registry.
2. Add `base_sha` parameter to each (edit the agent-tool JSON specs +
   handlers — same code path serves chat agent and MCP).
3. `seen_paths` enforcement: extend `app/llm/agents/_session.py`
   ContextVar to be settable from the MCP session. Same rule: file
   must have been `read_doc`'d at HEAD before edits.
4. `stale_paths` field — append to every tool result via a wrapper in
   the MCP tool dispatcher. (Agent-tool path doesn't need this; chat
   loop has direct state.)
5. `app/wiki/patch.py` + `app/mcp_server/tools/apply_patch.py`.
6. Tests: each tool with and without `base_sha`, stale rejection,
   atomic multi-edit rollback, patch with line-correct hunk, patch
   with line-drifted hunk (fuzzy fallback), patch with unresolvable
   hunk (atomic abort).

Exit criteria: an agent can read → edit with `base_sha` → succeed; can
read → someone else commits → edit fails with `stale_base`.

### Phase 5 — resources and subscriptions

1. Migration `0009_mcp_subscriptions.sql`.
2. `app/mcp_server/pubsub.py` — SQLite-backed registry + queue.
3. Hook `mcp_pubsub.publish_doc_update` into
   `commit_and_fan_out` (single line — every commit path flows through
   here, including the Huey worker process).
4. `app/mcp_server/resources.py` — `list`, `read`, `subscribe`,
   `unsubscribe` handlers.
5. Auto-subscribe in `read_doc` when `subscribe=true && is_head`.
6. SSE writer in `app/mcp_server/transport.py` polling
   `mcp_notifications` per session; ship `notifications/resources/updated`
   JSON-RPC frames.
7. Janitor: drop subscriptions and notifications older than 24h.
8. Tests: subscribe → another connection commits → SSE delivers the
   notification within 200ms; unsubscribe stops delivery; cross-process
   commit (Huey worker) reaches the server.

Exit criteria: two MCP clients connected; client A subscribes to a
doc; client B edits it; client A receives the push within 200ms.

### Phase 6 — async NL update

1. Migration `0008_mcp_jobs.sql`.
2. `app/mcp_server/jobs.py` repo.
3. `app/mcp_server/tools/update_doc_nl.py`.
4. `tasks/document_update.py:update_document_direct(job_id)` — wire
   the document-updater agent as documented in
   [agents/document-updater.md](../agents/document-updater.md).
5. Worker publishes job updates via `mcp_pubsub.publish_job_update`.
6. `job://<id>` resource handler returns the row.
7. Idempotency: unique index already on `(user_id, idempotency_key)`.
   Server-side debounce window check before calling LLM.
8. Tests: enqueue + poll, idempotency round-trip, debounce window, base
   sha stale rejection inside worker, NO_CHANGE path, LLM error path.

Exit criteria: agent calls `update_doc_nl`; receives `job_id`;
subscribes to `job://<id>`; gets pushed completion; can read the new
body.

### Phase 7 — polish

1. Rename `app/api/mcp.py` → `app/api/mcp_connections.py`; update
   `main.py` blueprint registration.
2. Update [seams.md](../seams.md) row to point at
   `app/mcp_server/__init__.py` for inbound; clarify the outbound
   surface is `app/api/mcp_connections.py`.
3. Add an `architecture_and_progress.md` row for the MCP server.
4. Operator docs: `docs/mcp-server.md` (or extend
   [running-locally](../running-locally.md)) — example client config
   for Claude Code (`~/.config/claude/mcp_servers.json`-style).
5. Frequency-hint language in tool descriptions
   (`<tool>.json:description`) — "Don't call after every commit; one
   batched edit per logical change."

## Testing

### Unit
- `tests/test_wiki_patch.py` — hunk parser, line-correct apply,
  line-drifted fuzzy apply, unresolvable hunk, atomic abort across hunks.
- `tests/test_mcp_pubsub.py` — subscribe / publish / drain semantics.
- `tests/test_mcp_tokens.py` — hashing, verify, revoke.
- `tests/test_mcp_jobs.py` — idempotency uniqueness, status transitions.

### Integration (against a tmp wiki repo + tmp DB)
- `tests/test_mcp_read_write.py` — `initialize` → `read_doc` →
  `edit_doc(base_sha)` → success; concurrent commit → stale rejection.
- `tests/test_mcp_subscriptions.py` — subscribe; commit from another
  session (and from a Huey worker with `huey.immediate=True`); assert
  notification fan-out via the SSE writer's outbound queue.
- `tests/test_mcp_nl_update.py` — patch `app.llm.client.complete` to
  return a synthetic body; round-trip job lifecycle; idempotency
  collapses retries; debounce skip.
- `tests/test_mcp_ask.py` — patch `app.llm.client.complete`; assert
  citations and that no write tools were callable.

Mocks: same rules as elsewhere — patch `app.llm.client.complete`, never
the SDK; tmp git repo for `wiki_git.*`; tmp SQLite for everything DB.

## Out of scope (do not pull forward)

- **Outbound MCP dispatch from triggers.** Trigger destinations are
  still null-only per [tool-design](../tool-design/tool-design.md);
  routing fires through MCP back to the originating agent is a separate
  feature.
- **Persistent subscriptions across reconnects.** Sessions die,
  subscriptions die. Add later if agent runtimes prove flaky.
- **Per-org isolation.** v0 has no org concept; tokens are user-scoped
  and that user can read/write everything not behind `@admin_required`.
- **Streaming partial results from `update_doc_nl`.** Job is
  pending/succeeded/failed; the agent doesn't need to watch the LLM
  emit tokens.
- **Resource templates** (`resources/templates/list`). The wiki tree
  is small; a flat `resources/list` is fine.

## Open questions

- **Streamable HTTP vs HTTP+SSE.** Some agent runtimes (older Claude
  Code builds) only speak the HTTP+SSE transport. We may need to
  support both during a transition period. Decision: ship Streamable
  HTTP first; add HTTP+SSE adapter only if we hit real client breakage.
- **Tool-description strategy.** Should we ship two parallel
  descriptions per shared tool (one optimized for the in-process chat
  agent, one for external coding agents) or share one? Default: share
  one, written for the external case (terser, MCP-protocol-aware).
  Revisit if the chat agent's tool-call accuracy regresses.
- **Rate limiting.** No quotas in v0. If a token starts spamming, we
  rely on the 30s NL debounce. Add `mcp_rate_limit` table + a
  middleware once we see abuse.
- **Tree representation in `resources/list`.** Flat list of every doc,
  or hierarchical via resource templates? Flat list scales fine to a
  few thousand docs; switch to templates beyond that.
- **`apply_patch` vs `edit_doc` — which do agents reach for?** Worth
  measuring once the surface is live; might inform whether we keep both
  long-term.
