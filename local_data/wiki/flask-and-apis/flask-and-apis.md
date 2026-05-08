# Flask + Basic APIs

> **Part of agent-wiki v0.** See the master doc
> [`../architecture_and_progress.md`](../architecture_and_progress.md) for
> the cross-area map (product spec, V0 brief, cross-cutting decisions).
> This doc owns the file-by-file design and progress for the HTTP surface,
> the Flask app factory, the auth middleware, the repos that back routes,
> and the migrations that define the schema. It does **not** cover trigger
> evaluation, chat agent logic, document-updater agent, or the frontend —
> those have their own per-area docs.

_Last updated: 2026-05-07_

---

## Design

### Surface

All routes are mounted under `/api`. Auth is required on everything except
explicit public endpoints (signup, login, `/auth/config`, inbound webhooks).

| Blueprint | Path prefix | Status |
|---|---|---|
| `auth`      | `/api/auth`      | real — signup/login/logout/me/config |
| `admin`     | `/api/admin`     | real — user CRUD + LLM settings (admin only) |
| `users`     | `/api/users`     | stub |
| `mcp`       | `/api/mcp`       | stub |
| `documents` | `/api/documents` | partial — list + read; write/delete/search/history TODO |
| `triggers`  | `/api/triggers`  | stub — see [natural-language-triggers](../natural-language-triggers/natural-language-triggers.md) |
| `events`    | `/api/events`    | stub |
| `webhooks`  | `/api/webhooks`  | stub |
| `chat`      | `/api/chat`      | real (stateless) — see [agents/chat-agent.md](../agents/chat-agent.md) |

### Architectural rules (also in CLAUDE.md)

- **Blueprints stay thin.** Parse → call domain module → serialize. No
  multi-step workflows in route handlers.
- **No raw `flask.session` reads** outside `app/auth/`. Use `@login_required`,
  `@admin_required`, `current_user()`.
- **No ORM.** Direct sqlite + small repo modules (pattern: `app/auth/users.py`).
  Repos return `sqlite3.Row` for reads, primitive ids for writes.
- **All schema changes** are new numbered files under `app/db/migrations/`.
  Never edit an applied migration. SQLite's ALTER is limited; rebuild the
  table when you need to drop/rename.
- **Errors** are `{"error": "<msg>"}` with the right status code. The
  frontend's `ApiError` parses this shape.

### File-by-file (current state)

#### `app/main.py`
Flask app factory. Calls `app.utils.logging.setup_logging()` first so every
subsequent boot step logs through the shared formatter. Configures session
cookie (httponly, samesite=Lax, 30-day permanent lifetime). On boot:
`init_db()` (run migrations) + `ensure_wiki_repo()` (init git in `WIKI_DIR`
if absent). Registers blueprints under `/api/{auth,admin,users,mcp,
documents,triggers,events,webhooks,chat}` and a `/api/health`.

#### `app/utils/logging.py`
`setup_logging(level=None)` — idempotent root logger config. Format:
`<ts> [<level>] <logger> (<file>:<line>): <msg>`. Level comes from the
`LOG_LEVEL` env var (default `INFO`). Called once each from
`app/main.py:create_app` and `app/tasks/run_worker.py:main`. Module code
uses the standard `log = logging.getLogger(__name__)` pattern; no module
should call `logging.basicConfig` or `print()` for diagnostics.

**Audit-event coverage** (where `log = logging.getLogger(__name__)` is now
declared and emits at INFO/WARNING/EXCEPTION):

- `app/api/`: `auth` (signup, login success/fail, signup race), `admin`
  (set_admin, delete user, llm settings update), `documents` (write,
  delete, folder create), `triggers` (create, update, delete), `events`
  (malformed payload warn), `webhooks` (placeholder).
- `app/auth/users.py` — user creation. `app/auth/passwords.py` — bcrypt
  rejects malformed hash.
- `app/db/sqlite.py` — each migration applied.
- `app/wiki/git.py` — repo init + seed; commit/delete at DEBUG.
  `app/wiki/filesystem.py` — path-traversal rejection (warning).
- `app/llm/client.py` — request (provider, model, tool count, msg count)
  and done (stop reason, token usage) at INFO; provider exceptions
  `log.exception`'d before being translated to `LLMError`.
  `app/llm/settings.py` — upsert.
  `app/llm/agents/chat.py` — tool dispatch failure (`log.exception`),
  iteration-limit hit (warning).

**DEBUG-level full-dump path (LLM observability).** Set `LOG_LEVEL=DEBUG`
to get untruncated, pretty-printed JSON of every LLM payload:

- `client.stream` entry → "llm request messages" + "llm request tools"
- `client.complete` exit → "llm response" (text + tool_calls + usage)
- `agents/chat._drive_loop` → "chat assistant turn" (text + tool_calls
  per iteration), "chat tool call" (per call, before dispatch), "chat
  tool result name=… id=…" (after dispatch, full content string).

Both `client.py` and `agents/chat.py` define a private `_debug_dump(label,
obj)` helper that gates serialization behind `log.isEnabledFor(DEBUG)`,
so this is free at INFO. Format is `json.dumps(obj, indent=2,
ensure_ascii=False, default=str)` — unicode preserved, no length cap.
- `app/tasks/`: `document_update`, `reindex`, `triggers`
  (fan-out summary + per-fire info), `periodic` (tick markers).
- `app/triggers/repo.py` — rebuild summary, skipped unreadable files.
  `app/triggers/natural_language.py` — `LLMError` warning before falling
  back to `matches=False`.

#### `app/config.py`
**Trimmed.** Now exposes only `secret_key`, `wiki_dir`, `app_db_path`,
`queue_db_path`, `auth_mode`, and OIDC fields. **LLM env keys were
removed** — provider/model/keys are DB-only via `app/llm/settings.py`.

> **Known bug:** `app/llm/settings.py:get()` still references
> `CONFIG.llm_provider` etc. in the no-row fallback. Will `AttributeError`
> on first boot until a row is inserted via the admin UI. Fix is
> work-unit "Stabilize the LLM seam" below.

#### `app/db/`
- `sqlite.py` — `connect()` returns an autocommit connection (WAL,
  foreign_keys=ON). `cursor()` context manager. `init_db()` is idempotent;
  applies any new `.sql` file under `migrations/` and records in
  `_migrations`.
- `fts.py` — `upsert_document`, `delete_document`, `search` over
  `documents_fts` (FTS5, porter+unicode61, bm25, snippet helper).
- `migrations/0001_init.sql` — `users`, `mcp_connections`, `documents`,
  `triggers`, `events` (+ indexes on `events.ts` and `events.kind+ts`),
  and `documents_fts` virtual table.
- `migrations/0002_admin_and_llm_settings.sql` — adds `users.is_admin`
  column; creates single-row `llm_settings` table.

#### `app/auth/` (real, end-to-end)
- `__init__.py` — `User` dataclass (with `is_admin`), `current_user()`
  reads session, `login_required`, `admin_required`.
- `users.py` — repo: `get_by_email`, `get_by_id`, `count`, `list_all`,
  `create` (auto-promotes the first user to admin), `set_admin`,
  `admin_count`, `delete`.
- `passwords.py` — bcrypt direct (avoids passlib/bcrypt incompat).
- `whitelist.py` — `ALLOWED_EMAILS` env, comma-separated, `*@domain` wildcard.
- `basic.py` — `authenticate(email, password) → User | None`.
- `oidc.py` — stub.

#### `app/api/`
- `auth.py` — **real**: `signup`, `login`, `logout`, `me`, public
  `/auth/config`. Returns `{is_admin}` on user payload.
- `admin.py` — **real**: list users, promote/demote (last-admin guard),
  delete (with self-delete + last-admin guards), get/put LLM settings
  (key redaction in responses; empty-string semantics for "leave
  existing key untouched").
- `documents.py` — **partial**: `GET /` returns `{paths: [...]}` from
  `wiki.git.list_paths(prefix)`; `GET /file?path=` reads the file directly
  off disk after `safe_rel_path`. `POST /reindex {path}` enqueues
  `tasks.reindex.reindex_path`. Everything else (`/search`, `GET /<id>`,
  `PUT /<id>`, `POST /ingest`, `/<id>/history`) is `NotImplementedError`.
- `users.py`, `mcp.py`, `triggers.py`, `events.py`, `webhooks.py` —
  stubs.
- `chat.py` — **real, stateless** (full impl in
  [agents/chat-agent.md](../agents/chat-agent.md)). Lives here for
  HTTP plumbing; `LLMError.code → status` mapping is the relevant local
  detail.

#### `app/wiki/` (real)
- `git.py` — `ensure_wiki_repo`, `commit_file`, `delete_path`,
  `read_file`, `history`, `list_paths`, `diff_for_commit`. All shell out
  to `git` via subprocess. Identity hardcoded `agent-wiki@local`.
- `filesystem.py` — `safe_rel_path`, `absolute`, `parent_dirs` (used for
  trigger fan-out to ancestor directories).
- `search.py` — wraps `db.fts.search` and exposes
  `bootstrap_index_if_empty()` for first-request indexing on a fresh
  install.

#### `app/models/`
Pydantic schemas: `Document`, `DocumentUpdate`, `IngestPayload`, `Trigger`,
`TriggerAction`, `User`, `Event`. HTTP shapes — not the DB layer.

#### `tests/`
Backend pytest harness. `conftest.py` provides per-test tmp DBs (`tmp_db`
fixture patches `CONFIG` in both `app.config` and `app.db.sqlite`, runs
migrations). Current coverage: LLM interface
(`test_llm_settings.py`, `test_llm_client.py`); SDKs are mocked at the
`_anthropic_client` / `_openai_client` seam — never import real provider
SDKs from tests.

### Auth model
- `AUTH_MODE = basic | oidc` (env). Basic is the only one wired.
- Sessions are Flask server-side cookies (`SESSION_COOKIE_HTTPONLY`,
  `SAMESITE=Lax`, 30-day permanent lifetime).
- bcrypt direct (no passlib). Min 8 chars on signup.
- Whitelist via `ALLOWED_EMAILS` env: empty = open, supports `*@domain` wildcard.
- First registered user is auto-promoted to admin (`users.create` checks
  `count() == 0`).
- Admin guards: cannot demote the last admin; cannot delete yourself; cannot
  delete the last admin.

### Schema (currently applied)

See the cross-area data-model table in
[the master doc](../architecture_and_progress.md#data-model-applied-schema).
This area owns: `users`, `mcp_connections`, `documents`, `events`,
`documents_fts`, `llm_settings`, `_migrations`. The `triggers` table is
maintained by [natural-language-triggers](../natural-language-triggers/natural-language-triggers.md).

### Open issues
- **`app/llm/settings.py:get()` references removed `CONFIG.*` fields.** First
  boot before any `llm_settings` row exists will `AttributeError`. Fix in
  the work unit below.

---

## Progress

### Working
- App factory + session config (`app/main.py`).
- Migrations runner (`app/db/sqlite.py:init_db`) — idempotent via
  `_migrations` table.
- Auth blueprint: signup, login, logout, me, `/auth/config`.
- Admin blueprint: list/promote/demote/delete users; get/put LLM settings
  with key-redaction in responses.
- Documents blueprint: `GET /` (list paths), `GET /file?path=` (read body),
  `POST /reindex` (enqueue reindex).
- `wiki/git.py` wrapper exposes `delete_path()` for the upcoming delete
  endpoint.

### Stubbed
- `users`, `mcp`, `triggers`, `events`, `webhooks` blueprints all
  `NotImplementedError`.
- Documents `search`, `get_by_id`, `update`, `ingest`, `history` —
  `NotImplementedError`.

### Not started
- Move/rename docs.
- Real OIDC support.
- Permissioning beyond admin / non-admin.
- Concurrent-edit conflict handling.
- Sync LLM "draft a plan" helper API.

---

## Work breakdown (Next up)

Each item is sized to be a coherent PR. Order roughly reflects dependencies.
Work units are lettered to match the original cross-area roll-up; the same
letters appear in other per-area docs when the unit cuts across.

### A. Stabilize the LLM seam
1. **Fix `app/llm/settings.py:get()` no-row path.** It references removed
   `CONFIG.*` fields. Either (a) seed a default `llm_settings` row in
   migration `0002` with empty keys + sensible `provider` / `model`, or
   (b) return `LLMSettings(provider="", model="", "", "")` and let
   `complete()` raise a friendly "configure LLM in admin" error.
2. **`/api/admin/llm` GET should not 500** if there's no row yet — confirm
   path (a) or (b) handles this.

### B. Wiki write path
1. **Add `PUT /api/documents/file?path=`** that takes `{body, message?}`,
   validates path, writes via `wiki.git.commit_file`, then enqueues
   `tasks.reindex.reindex_path` (already exists; derives title from the
   first `# heading`).
2. **Backfill `documents` row** on every commit (insert-or-update by path).
   Currently nothing inserts into the table.
3. **Add `DELETE /api/documents/file?path=`** — `wiki.git.delete_path` +
   commit + remove FTS row + remove `documents` row.
4. **Implement `GET /api/documents/search`** — wraps `db.fts.search`,
   returns the snippet structure as-is. The chat search tool will use this.
5. **`GET /api/documents/history?path=`** — wraps `wiki.git.history`.

### E.1 Events API (UI in [frontend](../frontend/frontend.md))
- `GET /api/events?kind=trigger.fire&since=&until=&cursor=&limit=` over
  the events table. Cursor over `id`, descending. Time-based filters per
  the V0 brief.

### I. Webhooks + ingest API surface (V0 brief)
- `POST /api/webhooks/<source>` — verify per-source signature, record an
  `events` row of kind `webhook.in`, enqueue downstream work.
- `POST /api/documents/ingest` — generic connector update (depends on the
  push contract — see [onyx-push](../onyx-push/onyx-push.md)).
- These two share the request → event → task pipeline; pick the shared shape.

### J. Sync LLM "draft a plan" API (V0 brief)
- Small `POST /api/llm/draft` endpoint that takes
  `{instruction, context_docs?}` and returns a single `complete()` result.
- Frontend exposure can wait until the editor lands.

### K.1 MCP connection management API (V0 brief)
- Real `app/api/mcp.py`: list/create/delete per-user connections.
- Admin/settings UI is owned by [frontend](../frontend/frontend.md).
- Out of scope to actually use MCP from agents in v0 (that's
  [exploration](../exploration/exploration.md)).

### M. Test harness expansion
1. **End-to-end tests:** auth (signup/login/me), wiki read+write commits,
   FTS reindex (set `huey.immediate = True`).
2. **LLM seam tests** — patch `app.llm.client.complete` to return canned
   responses for trigger eval, chat, document-updater tests.
3. **Trigger eval test pattern** — patch with canned `{matches, reason}`
   JSON.

### Backlog
- Move/rename docs.
- Real OIDC support.
- Permissioning beyond admin / non-admin.
- Concurrent-edit conflict handling.
- Editor diff preview on Save.
