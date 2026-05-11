# MCP Server (inbound)

The wiki exposes itself **as** an MCP server so external coding agents
(Claude Code, Cursor, Codex, custom harnesses) can discover, read, and
edit wiki docs as a first-class workspace.

This is the **inbound** surface. Distinct from the outbound MCP-client
surface (`backend/app/api/mcp_connections.py`) which lets *our* in-process
agent harness consume *other* MCP servers.

> **Status — Phases 1–7 shipped (full surface).** Authentication,
> JSON-RPC transport, read tools, write tools, resource subscriptions
> with SSE notifications, the async ``update_doc_nl`` job, and the
> operator + tool-description polish are all live. Open questions
> below track follow-on work that wasn't in scope for v0.

## What it currently supports

External agents authenticated with a personal API token can:

- **Discover** — full-text search the wiki via BM25, list a doc's
  commit history, and ask natural-language questions answered by a
  curated read-only sub-agent.
- **Read** — fetch HEAD or any historical sha of a markdown doc.
- **Edit (sync)** — surgical find-and-replace (`edit_doc`), atomic
  multi-edit batches (`multi_edit`), full-body overwrite or create
  (`write_doc`), line-anchored unified-diff patches (`apply_patch`),
  rename / move (`move_path`), and folder creation (`create_directory`).
- **Stay safe** — every write tool accepts a `base_sha` parameter for
  optimistic concurrency. `write_doc` *requires* `base_sha` on overwrites
  because full-body writes have no fuzzy fallback. The same
  `assert_base_sha` helper that backs the chat agent backs the MCP
  surface.
- **Stay in scope** — every tool consults `app/wiki/acl.py`, so an MCP
  token can read or modify exactly what the same user could via the web
  UI. Search results are pre-filtered through ACL in SQL.
- **Stay in sync** — `resources/list`, `resources/read`,
  `resources/subscribe`, `resources/unsubscribe` over `wiki:///<path>`
  and `job://<id>` URIs. `read_doc` auto-subscribes the session at
  HEAD by default. Commits anywhere — UI saves, chat-agent edits,
  MCP edits, task worker writes — fan out to subscribed sessions
  over a long-lived SSE stream on `GET /api/mcp` as
  `notifications/resources/updated` (and `…/list_changed` on creates,
  deletes, and moves). Postgres `LISTEN/NOTIFY` carries cross-process
  events from the worker to the web replica that owns the session's
  SSE stream.
- **Hand off heavy work** — `update_doc_nl(path, instruction,
  base_sha?, idempotency_key?)` enqueues an async job, returns
  `{job_id, status_uri: "job://<id>"}`, and auto-subscribes the
  calling session so the SSE stream pushes status changes
  (`pending` → `succeeded` | `failed`). The worker reconstitutes
  `g.user` from the job row before any wiki write, so ACL applies
  inside the worker too. Idempotency: same `(user, path,
  instruction)` collapses onto the same job; explicit
  `idempotency_key` overrides. Server-side debounce (default 30s)
  skips redundant LLM calls when the same `(user, path)` was just
  committed.

The surface that *isn't* available yet: final polish (Phase 7) —
operator-facing docs and tool-description tuning. See [What's not yet
implemented](#whats-not-yet-implemented).

## Architecture — request lifecycle

Two concurrent flows hang off `/api/mcp`: the per-call request/response
on `POST` and the long-lived SSE side channel on `GET`. Both wear the
same bearer auth.

### POST — request/response

```
┌─────────────────────────────────────────────────────────────────┐
│  External agent (Claude Code / Cursor / Codex)                  │
│  POST /api/mcp                                                   │
│  Authorization: Bearer mcp_<token>                               │
│  Mcp-Session-Id: <id>          (after first initialize)         │
│  body: {jsonrpc:2.0, id, method, params}                         │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
   ┌──────────────────────────────────────────────────────┐
   │  app/api/mcp_server.py  — FastAPI router @ /api/mcp  │
   └──────────────────────────────┬───────────────────────┘
                                  │
                                  ▼
   ┌─────────────────────────────────────────────────────────┐
   │  app/auth/deps.py:require_bearer (FastAPI Depends)      │
   │  → tokens_repo.verify(raw) → returns User               │
   │                                                         │  ← single seam:
   │  401 if missing / unknown / revoked                     │    the route then
   │                                                         │    enters
   │  Route body: `with set_current_user(user):`             │    set_current_user(...)
   │  binds current_user_ctx (the same ContextVar            │    so every helper
   │  CurrentUserMiddleware sets on cookie-authed requests)  │    below reads the
   └──────────────────────────────┬──────────────────────────┘    user via
                                  │                               current_user()
                                  ▼
   ┌─────────────────────────────────────────────────────────┐
   │  app/mcp_server/transport.py — dispatch(message)        │
   │  - validates jsonrpc==2.0                               │
   │  - routes by method:                                    │
   │      initialize → mcp_session.create(current_user())    │
   │      notifications/initialized → flips initialized=True │
   │      tools/list, tools/call → mcp_tools                 │
   │      resources/list, /read, /subscribe, /unsubscribe    │
   │                              → mcp_resources            │
   │      ping / errors                                      │
   └──────────────────────────────┬──────────────────────────┘
                                  │
                                  ▼
   ┌─────────────────────────────────────────────────────────┐
   │  app/mcp_server/tools.py                                │
   │  - allow-list filter (MCP_ALLOWED_TOOLS)                │
   │  - registry_dispatch(name, args)                        │
   │  - auto-subscribe on read_doc(is_head=true)             │
   │  - wrap result: {content:[{type:text,text:json(...)}],  │
   │                  isError, stale_paths}                  │
   └──────────────────────────────┬──────────────────────────┘
                                  │
                                  ▼
   ┌─────────────────────────────────────────────────────────┐
   │  app/llm/agents/tools/<name>.py — SHARED with chat agent│
   │  - validate args                                        │
   │  - require_can("read"|"write", path)  ← ACL gate        │
   │  - assert_base_sha(rel, base_sha)                       │
   │  - body manipulation (wiki.edit / wiki.patch)           │
   │  - commit_and_fan_out → wiki.git + wiki.notify          │
   └──────────────────────────────┬──────────────────────────┘
                                  │
                                  ▼
   ┌─────────────────────────────────────────────────────────┐
   │  app/wiki/notify.py:after_doc_write / _delete / _move   │
   │  - reindex_path (BM25)                                  │
   │  - fan_out_trigger_eval                                 │
   │  - acl.on_page_created / _deleted / _moved              │
   │  - mcp_pubsub.publish_doc_update / _delete /            │
   │                                  publish_list_changed   │
   └─────────────────────────────────────────────────────────┘
```

### GET — SSE side channel + pub-sub

```
┌─────────────────────────────────────────────────────────────────┐
│  External agent — long-lived GET /api/mcp                       │
│  Authorization: Bearer mcp_<token>                               │
│  Mcp-Session-Id: <id>                                            │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼  Depends(require_bearer) + session check
   ┌─────────────────────────────────────────────────────────┐
   │  app/api/mcp_server.py:transport_sse                    │
   │  - per-iteration: pubsub.drain_blocking(sess_id, 15s)   │
   │  - yield "data: {jsonrpc:2.0, method, params}\n\n"      │
   │  - heartbeat ":keepalive\n\n" on timeout                │
   │  - on disconnect → mcp_session.drop(sess_id)            │
   │                  → pubsub.forget(sess_id)               │
   └──────────────────────────────▲──────────────────────────┘
                                  │ in-memory queue per session
                                  │
   ┌──────────────────────────────┴──────────────────────────┐
   │  app/mcp_server/pubsub.py                               │
   │  - subscriptions: session_id → set[rel_path]            │
   │  - reverse index: rel_path → set[session_id]            │
   │  - per-session queue: drain_blocking(...) parks here    │
   │  - publish_*  : local fan-out + per-subscriber ACL      │
   │                 recheck (acl.can(...)) before delivery  │
   │                 + NOTIFY wiki_commit, '<json>'          │
   └──────────────────────────────▲──────────────────────────┘
                                  │
              ┌───────────────────┴────────────────────┐
              │                                        │
   ┌──────────┴──────────┐               ┌─────────────┴──────────┐
   │ in-process commits  │               │ cross-process commits  │
   │ (web → web):        │               │ (worker → web):        │
   │ direct call into    │               │ LISTEN wiki_commit     │
   │ publish_doc_update  │               │ thread per web replica │
   │ from after_doc_write│               │ re-publishes locally   │
   └─────────────────────┘               └────────────────────────┘
```

The handler layer is **shared** with the in-process chat agent. The MCP
server is structurally a different transport / authentication front-end
plus an allow-list — every wiki side-effect goes through the same code
the chat agent uses, so ACL, agent-activity attribution, BM25 reindex,
trigger fan-out, **and pub-sub fan-out** all apply identically. A
chat-agent edit fires the same `notifications/resources/updated`
frame to MCP subscribers as an MCP edit would.

## Authentication — bearer tokens

Personal API tokens are per-user, hashed-at-rest, shown to the user once
at creation and never persisted.

- Schema: `mcp_tokens(id, user_id, name, token_hash, created_at, last_used_at)`
  — see `app/db/models.py:McpToken` and migration `0003_mcp_tokens.py`.
- Repo: `app/auth/mcp_tokens.py` — `create()`, `list_for_user()`,
  `revoke()`, `verify()`.
- HTTP: `GET / POST / DELETE /api/mcp/tokens` —
  `app/api/mcp_tokens.py`, `Depends(require_user)` (cookie-authenticated
  user managing their own tokens).
- Format: `mcp_<24-byte-urlsafe>` plaintext, distinguishable in logs.
- Verification (`verify`): bcrypt-walks every row and bumps
  `last_used_at` on the matching one. Linear scan is fine at the
  per-user-keys-each scale; revisit if we ever ship machine-generated
  tokens at volume.

The bearer dependency (`app/auth/deps.py:require_bearer`) is the
**single seam** that turns a token into a request principal. It
resolves the token to a `User`; the MCP route then wraps its dispatch
in `with set_current_user(user):`, which binds the same
`current_user_ctx` ContextVar that `CurrentUserMiddleware` populates
on cookie-authed requests. Everything below this line (`require_can`,
`commit_and_fan_out`, agent-activity attribution, trigger `actor`
field, frontmatter rendering) reads `app.auth.current_user()` and
sees the right user with zero MCP-specific code.

## Authorization — ACL is the same as the web UI

There is **no MCP-specific ACL code path.** Once `current_user_ctx` is
bound, every existing helper in `app/wiki/acl.py` and
`require_can(action, path, user)` fires unchanged.

| MCP surface | Check |
| --- | --- |
| `read_doc(path, sha?)` | `require_can("read", path)`. Returns the standard `forbidden` error envelope. |
| `list_history(path)` | `require_can("read", path)`. |
| `search_wiki(query)` | Filtered through `acl.visible_paths_filter` — the BM25 query joins ACL in SQL, unauthorized hits never get scored or returned. |
| `ask_nl_question(query)` | The wiki_qa harness's read tools all go through the same gates because they read `current_user()`. |
| `edit_doc` / `multi_edit` / `apply_patch` | `require_can("write", path)` enforced inside `commit_and_fan_out`. |
| `write_doc` (overwrite) | `require_can("write", path)`. |
| `write_doc` (create) | Allowed for any authenticated user. New page gets default-public ACL rows + an owner stamp via the lifecycle hook in `app/wiki/notify.py:after_doc_write(change_kind="create", owner_user_id=user.id)` — see the [permissions doc](../permissions/permissions.md) for the rationale. |
| `move_path` | `require_can("write", old)` (and creates owner rows on the new path). |
| Token CRUD (`/api/mcp/tokens`) | `Depends(require_user)` — a user can only mint and revoke their own tokens. |

Admins still bypass page-level checks per the permissions doc, mirroring
the web UI.

## Session lifecycle

Sessions are **in-memory and process-local** in
`app/mcp_server/session.py`. A `McpSession` carries `(id, user_id,
is_admin, initialized)`. They die when the process restarts —
persistent subscriptions across reconnects are an explicit non-goal.

```
client                                 server
  │                                       │
  │── POST initialize ───────────────────▶│  Depends(require_bearer) → user
  │                                       │  set_current_user(user)
  │                                       │  mcp_session.create(current_user())
  │                                       │  → returns sess (initialized=False)
  │◀─ result + Mcp-Session-Id: <new id> ──│
  │                                       │
  │── POST notifications/initialized ────▶│  sess.initialized = True
  │   (Mcp-Session-Id: <id>)              │
  │◀─ 202 Accepted (no body) ─────────────│
  │                                       │
  │── POST tools/list ───────────────────▶│  filter MCP_ALLOWED_TOOLS;
  │                                       │  reshape input_schema → inputSchema
  │◀─ result: {tools: [...]} ─────────────│
  │                                       │
  │── POST tools/call ───────────────────▶│  registry_dispatch(name, args)
  │   {name, arguments}                   │
  │                                       │  wrap in {content,isError,stale_paths}
  │◀─ result: {content:[...]} ────────────│
```

For multi-replica deploys, the load balancer must pin sessions to a
replica via `Mcp-Session-Id` (e.g. sticky cookie, header-hash rule) —
v0 ships single-process so this is automatic and not a concern yet.

## JSON-RPC transport

Hand-rolled in `app/mcp_server/transport.py` rather than via the `mcp`
Python SDK. The SDK is async-first and prescribes its own ASGI mount
shape — hosting it alongside our existing FastAPI routes would
require either letting the SDK own a sub-app or wiring an awkward
adapter between its session manager and our auth/session model. Too
much infra for what is currently six methods (`initialize`,
`notifications/initialized`, `ping`, `tools/list`, `tools/call`, plus
errors). The MCP doc's Phase-2 fallback path explicitly allows this.

If/when the surface grows enough that the SDK pays off — or a future
client expects strictly spec-compliant capability negotiation we
haven't reproduced — the transport module is small enough to swap
behind the same router without touching callers.

Spec: MCP `2025-03-26`. Errors follow JSON-RPC 2.0:
`-32600 INVALID_REQUEST`, `-32601 METHOD_NOT_FOUND`, `-32602
INVALID_PARAMS`, `-32603 INTERNAL_ERROR`. Application-level errors
(file not found, permission denied, `stale_base`) come back inside the
result envelope with `isError: true` — JSON-RPC errors are reserved
for protocol-level issues.

## Tool surface

Tool *handlers* live in the chat agent's existing registry at
`app/llm/agents/tools/`. The MCP server doesn't duplicate them; it
re-exports a curated subset via `app/mcp_server/tools.py:MCP_ALLOWED_TOOLS`.
Adding a tool to MCP requires its name to be added to the allow-list —
walking the chat-agent registry blindly would expose tools that don't
make sense over MCP yet (e.g. `run_bash`, `web_search`).

| Tool | Purpose | MCP-specific extras |
| --- | --- | --- |
| `read_doc(path, sha?)` | Read HEAD or a historical sha. Returns `{path, body, sha, is_head}`. Pass the returned HEAD `sha` to a subsequent edit as `base_sha` for optimistic concurrency. | `sha` parameter for historical reads. |
| `search_wiki(query, limit?)` | BM25 search filtered through ACL. Returns `{path, title, snippet, score}`. | — |
| `list_history(path, limit?)` | Per-path commit history. Returns `[{sha, author, ts, message, deprecated_by}, ...]`. | — |
| `ask_nl_question(query)` | RAG-style answer with sources. Wraps `app.llm.agents.wiki_qa` (read-only chat-loop instance). | — |
| `edit_doc(path, old_string, new_string, commit_message, base_sha?)` | Surgical fuzzy find-and-replace. | `base_sha` (optional) for optimistic concurrency. |
| `multi_edit(path, edits[], commit_message, base_sha?)` | Atomic batch of `{old_string, new_string, replace_all?}` edits. | `base_sha` (optional). |
| `write_doc(path, body, commit_message, base_sha?)` | Full-body overwrite or create. | `base_sha` **required** when overwriting (no fuzzy fallback). |
| `apply_patch(path, patch, commit_message, base_sha?)` | Line-anchored unified diff with content-based fuzzy fallback (`app/wiki/patch.py`). | `base_sha` (optional). |
| `move_path(old_path, new_path, commit_message)` | Rename a doc or folder. | — |
| `create_directory(path, commit_message)` | Create an empty folder via `.gitkeep`. | — |
| `update_doc_nl(path, instruction, base_sha?, idempotency_key?)` | Async LLM-driven update. Returns `{job_id, status_uri: "job://<id>", status, deduplicated}`. Worker runs the document-updater agent and commits if it produces a new body. | `idempotency_key` (defaults to `sha256(user_id\|path\|instruction)`) for retry collapse; `job://<id>` resource subscribe for status pushes; server-side debounce (`MCP_NL_DEBOUNCE_SECONDS`, default 30s). |

`tools/list` translates the internal JSON specs (`input_schema` →
`inputSchema` per MCP spec) and filters to the allow-list.
`tools/call` dispatches into the same handler the chat agent uses.

## Concurrency — the staleness model

Two layers, strongest to weakest:

### 1. `base_sha` optimistic concurrency — hard correctness

Every write tool accepts an optional `base_sha`. When set, the handler
calls `_doc_helpers.assert_base_sha(rel, base_sha)`; if `base_sha` no
longer matches HEAD for `rel`, the call returns:

```json
{
  "error": "stale_base",
  "base_sha": "<what agent sent>",
  "current_sha": "<head>",
  "message": "the file has changed since base_sha; re-read with read_doc, re-derive the edit, and retry"
}
```

`base_sha` semantics by tool:

| Tool | base_sha behavior |
| --- | --- |
| `edit_doc` | Optional. Recommended over MCP. The fuzzy `old_string` chain is the only safety net without it. |
| `multi_edit` | Optional. Same as `edit_doc`. |
| `apply_patch` | Optional. Line-anchored hunks + content-based fallback give partial safety; `base_sha` makes drift detectable. |
| `write_doc` (overwrite) | **Required.** Without it, returns `{"error": "base_sha_required_for_overwrite"}` — full-body writes have no fuzzy fallback, so the staleness check can't be skipped. |
| `write_doc` (create) | N/A — file doesn't exist yet. |
| `move_path` | N/A — content unchanged. |

`read_doc` returns the current HEAD sha so an agent can immediately
round-trip it: `read_doc → edit_doc(base_sha=that_sha)`. The returned
sha is captured **after** the agent-activity frontmatter refresh runs
so it always matches the body returned to the agent.

### 2. `stale_paths` field — belt-and-suspenders

Every MCP tool result carries a `stale_paths` array — paths the
session is subscribed to that have a pending update since the last
tool call. Computed by non-destructively peeking at the session's
pub-sub queue: every queued notification is read, paths with
`wiki:///<path>` URIs are collected, then the notifications are put
back so the SSE writer still ships them. An attentive client that
actively drains its SSE stream will see `stale_paths: []` in steady
state.

## Module layout

```
backend/app/mcp_server/
  __init__.py           Package marker + status doc
  session.py            McpSession + in-memory registry, all_session_ids
  transport.py          JSON-RPC dispatcher (initialize, tools/*,
                        resources/*, ping, errors) — receives the
                        bearer-resolved User as an explicit arg from
                        the FastAPI route
  tools.py              MCP_ALLOWED_TOOLS allow-list,
                        list_for_mcp() (input_schema → inputSchema),
                        call_for_mcp() (auto-subscribes read_doc,
                                        computes stale_paths)
  resources.py          list / read / subscribe / unsubscribe handlers
                        for wiki:///<path> AND job://<id> URIs
  pubsub.py             Subscription registry + per-session queue +
                        Postgres LISTEN/NOTIFY bridge
                        (start_listener() / stop_listener())
                        + publish_job_update for job:// subscribers
  jobs.py               Async-job repo (create / get / update / dedupe by
                        idempotency_key / debounce window lookup)

(The worker rebinds `current_user_ctx` directly via
`app.auth.set_current_user(load_user(uid))` — see
`backend/app/tasks/document_update.py`. No process-local request-context
shim is needed — the ContextVar carries the principal across the
worker call.)

backend/app/api/
  mcp_server.py         POST /api/mcp + GET /api/mcp (SSE) FastAPI router
  mcp_tokens.py         GET / POST / DELETE /api/mcp/tokens (cookie auth)
  mcp_connections.py    Outbound MCP-client connection list (existing,
                        renamed from mcp.py; mounted at /api/mcp/connections)

backend/app/auth/
  mcp_tokens.py         Token repo: create / list_for_user / revoke / verify

backend/app/db/migrations/versions/
  0001_initial.py       Bootstraps every ORM table via Base.metadata.create_all,
                        so a fresh DB picks up McpToken / McpJob without
                        needing intermediate migrations
  0004_mcp_jobs.py      Explicit ALTER for production DBs that ran 0001
                        before McpJob was added to the model

backend/app/db/models.py
  McpToken              Personal API token (matches McpConnection style)
  McpJob                Async job row + partial unique index on
                        (user_id, idempotency_key)

backend/app/wiki/notify.py
  after_doc_write       + mcp_pubsub.publish_doc_update / publish_list_changed
  after_doc_delete      + mcp_pubsub.publish_doc_delete / publish_list_changed
  after_path_move       + per-pair update/delete + publish_list_changed

backend/app/llm/agents/tools/_doc_helpers.py
  assert_base_sha       Shared optimistic-concurrency helper used by every
                        write tool; chat agent + MCP both go through it

backend/app/main.py
  create_app()          Calls mcp_pubsub.start_listener() once at boot
                        (web process only) so the worker's commits reach
                        SSE subscribers via NOTIFY wiki_commit

backend/app/tasks/document_update.py
  agent_update_document_nl(job_id)
                        Worker task on documents_queue. Loads job row,
                        runs `with set_current_user(load_user(job.user_id)):`,
                        validates base_sha + debounce, invokes
                        document_updater, marks succeeded / failed,
                        publishes job state changes via pubsub.
```

Frontend:

```
frontend/src/
  app/agents/page.tsx          /agents route — copy block, generate / reveal-once,
                               key list with revoke, sample client config
  lib/agents.ts                useTokens() SWR hook + create / revoke + endpoint URL
  components/common/AppShell.tsx  + Agents NavItem in the left rail
```

## Frontend — the Agents page

A top-level sidebar entry rather than a buried `/settings/*` page —
"give your agents the ability to read and update the wiki" is the
user-facing concept; tokens are the implementation detail.

Layout:

1. **Hero copy.** "Give your agents the ability to read and update this
   wiki. Generate a personal API key below, then drop it into your
   coding agent's MCP configuration."
2. **Endpoint block.** Read-only display of `${origin}/api/mcp` with a
   copy-to-clipboard button. Caption explains the
   `Authorization: Bearer mcp_…` header.
3. **Generate API key** button → modal with a name field → reveal-once
   panel with the plaintext token, copy button, and "you won't see this
   again" warning.
4. **Existing keys list** — one row per token: name, created_at,
   last_used_at (or "never used"), revoke button (with confirm).
5. **Collapsible sample config** for `mcp_servers.json` — copy-pastable
   stanza pointing at the endpoint with a placeholder for the bearer
   token.

What the page does **not** include in v1:

- Per-token permission scoping (read-only keys, path-prefix keys).
  Tokens inherit the minting user's permissions in full; see [open
  questions](#open-questions).
- Usage analytics, request logs, rate limits.
- Admin "see all tokens across users" view. Strictly the current user's
  tokens. Admin-side visibility (if needed) would land at
  `/admin/agents` later.

## Testing

Backend unit + integration tests against per-test Postgres schemas, real
git, and FastAPI's `TestClient`. No SDK mocking — these exercise the
HTTP transport end-to-end.

| File | Coverage |
| --- | --- |
| `tests/test_mcp_tokens.py` | Repo round-trip, hash collision, revocation, multi-user isolation, `last_used_at` bump, plus HTTP layer (401 unauth, 201 reveal-once, 400 validation, 204 revoke, 404 second-revoke, cross-user revoke = 404). |
| `tests/test_mcp_server_transport.py` | Bearer auth (no header / wrong scheme / unknown / revoked), `initialize` shape, `Mcp-Session-Id` header, full handshake → tools/list → ping flow, missing-session protocol error, pre-initialized rejection, unknown method, missing `jsonrpc` field, non-dict body rejection, `current_user()` visible inside dispatch. |
| `tests/test_mcp_server_tools.py` | `tools/list` shape (`inputSchema` not `input_schema`) and allow-list, `read_doc` HEAD vs historical, ACL forbid on `read_doc` and `list_history`, disallowed and unknown tool rejection, `tools/call` parameter validation, search round-trip via MCP. |
| `tests/test_mcp_server_writes.py` | End-to-end read → edit_doc(base_sha) → success; stale base_sha rejection; edit_doc without base_sha; multi_edit happy path + atomic abort; apply_patch with correct base_sha; write_doc create vs overwrite; `base_sha_required_for_overwrite`; ACL forbid on write; `stale_paths` present on every result. |
| `tests/test_mcp_server_subscriptions.py` | `resources/list` (ACL filtered, admin sees all); `resources/read` (HEAD body, forbidden, malformed URI); explicit subscribe + publish round-trip; auto-subscribe via `read_doc(subscribe=true)`, suppressed for historical reads and `subscribe=false`; `unsubscribe` stops delivery; per-subscriber ACL recheck drops post-revoke; end-to-end `edit_doc` → `after_doc_write` → push lands in subscriber's queue; `create` fires `list_changed`; `stale_paths` lists pending paths non-destructively; SSE stream rejects pre-init, requires correct session ownership, delivers a frame, cleans up on disconnect. |
| `tests/test_mcp_server_jobs.py` | `update_doc_nl` enqueue → worker → succeed-with-commit; `NO_CHANGE` path; missing instruction / unread file / blocked ACL rejected at enqueue; explicit-key idempotency collapses retries; default key collapses identical retries; different instructions get different jobs; worker rechecks `base_sha` and fails with `stale_base` on drift; debounce skips redundant commits within the window; `resources/read job://<id>` returns the public view (no `user_id`); cross-user reads return not-found; cross-user subscribe returns forbidden; the wrapper auto-subscribes the calling session and the SSE queue receives the terminal status. |
| `tests/test_doc_tools.py` | Chat-agent regression — confirms `base_sha` round-trip works for the in-process loop too (write_doc with sha succeeds; without sha returns `base_sha_required_for_overwrite`; with stale sha returns `stale_base`). |

Run the targeted set: `uv run --extra dev pytest
tests/test_mcp_*.py tests/test_doc_tools.py`.

Full suite: `uv run --extra dev pytest`.

## Out of scope (do not pull forward)

- **Outbound MCP dispatch from triggers.** Trigger destinations are
  still null-only per [tool-design](../tool-design/tool-design.md).
- **Persistent subscriptions across reconnects.** Sessions die,
  subscriptions die. Add later if agent runtimes prove flaky.
- **Per-org isolation.** v0 has no org concept; per-user ACL via
  `app/wiki/acl.py` is the only isolation mechanism.
- **Streaming partial results from `update_doc_nl`.** Job is
  pending/succeeded/failed; the agent doesn't need to watch the LLM
  emit tokens.

## Open questions

- **Tool-description strategy.** Should we ship two parallel
  descriptions per shared tool — one optimized for the in-process chat
  agent, one for external coding agents — or share one? Default: share
  one. Revisit if the chat agent's tool-call accuracy regresses.
- **Rate limiting.** No quotas in v0. Add `mcp_rate_limit` table + a
  middleware once we see abuse.
- **Tree representation in `resources/list`** (Phase 5). Flat list of
  every doc, or hierarchical via resource templates? Flat list scales
  to a few thousand docs; switch to templates beyond that.
- **`apply_patch` vs `edit_doc` — which do agents reach for?** Worth
  measuring once external traffic is real; might inform whether we
  keep both long-term.
- **Per-token permission scoping.** v1 tokens inherit the minting
  user's permissions in full. A user might want a read-only key for an
  agent or a key scoped to `/specs/`. Add a `scope` column on
  `mcp_tokens` (`{actions, paths_under}`) consulted *in addition to*
  the user's ACL — never broader, only narrower. Out of scope until
  users ask.
