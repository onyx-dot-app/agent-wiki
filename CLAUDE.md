# CLAUDE.md

> **The `local_data/wiki/` directory is the source of truth** for product/UX
> intent, architectural decisions, work ownership, and what's actually built
> vs. planned. Browse it at the start of every session and reference the
> relevant pages throughout your work. **Keep it up to date** for every
> change, update, or decision — when you finish a piece of work, make a
> decision, or invalidate an assumption, edit the appropriate page (or add
> a new one) so the wiki reflects current reality. CLAUDE.md is the durable
> rulebook; `local_data/wiki/` is the living state.

Guidance for Claude (and other agents) working on **agent-wiki** — a
self-updating wiki for AI agents. Read this before changing code.

## Stack at a glance

- **Backend** — Flask + SQLite (FTS5) + Huey on SQLite. Git is shelled out to.
- **Frontend** — Next.js 14 (App Router) + TypeScript.
- **Nginx** in front, reverse-proxying `/api/*` → backend, everything else → frontend.
- Two volumes: `app-data` (`app.sqlite` + `queue.sqlite`) and `wiki-data` (the git-backed wiki working tree).

See `docs/architecture.md` for the data flows.

## Run it

```bash
cp .env.example .env       # set SECRET_KEY; ALLOWED_EMAILS optional
docker compose up --build
```

App at http://localhost:8080. First account created is auto-promoted to admin
(see `users.create` in `backend/app/auth/users.py`). LLM provider/keys are
configured at runtime in the admin UI — env vars are only the fallback before
any row exists in `llm_settings`.

## Pre-commit hooks

Lint config lives in `.pre-commit-config.yaml`; CI runs the same hooks on PRs.
Run `pre-commit install` once, then `pre-commit run --all-files` ad-hoc.
Add new checks as hooks here, not as one-off CI steps.

## Layout

```
backend/app/
  api/            Flask blueprints — thin HTTP layer, no business logic
  auth/           sessions, bcrypt, whitelist, admin flags
  db/             sqlite + FTS5 + numbered .sql migrations
  llm/            provider-agnostic LLM client + DB-backed settings + agents
  models/         pydantic schemas (request/response shapes)
  tasks/          Huey tasks (workers run in their own container)
  triggers/       NL-trigger evaluation engine
  wiki/           git subprocess wrapper + path utilities + search
frontend/src/
  app/            Next.js routes
  components/     UI components (AppShell, etc.)
  lib/            api.ts, auth.tsx — the only place pages talk to the network/auth
  types/          shared TS types
nginx/            reverse proxy
wiki/seed/        sample content for fresh installs
docs/             architecture + API reference
```

## Architectural rules — required interfaces and seams

These exist so the system stays testable and swappable. Honor them.

### LLM calls — always through `app/llm/client.py`

`stream(messages, ...)` and `complete(messages, ...)` (a drainer) in
`app/llm/client.py` are the **only** allowed entry points for talking to a
model. They yield/return a normalized shape (`text_delta`/`tool_call`/`done`
events; `{text, tool_calls, stop_reason, usage}` dicts) so callers don't
branch on provider.

Provider implementations live as a plural seam under `app/llm/providers/`:
one module per backend (`anthropic.py`, `openai.py`, `gemini.py`, `ollama.py`),
each exposing a module-level `PROVIDER` satisfying the `Provider` protocol
(`name`, `check_configured(settings)`, `stream(messages, *, model, tools,
max_tokens, settings)`).

- Do **not** `import anthropic`, `import openai`, `from google import genai`,
  or `import ollama` outside the matching `app/llm/providers/<name>.py` module.
- Provider, model, and credentials come from `app/llm/settings.py:get()`
  (DB-backed, configured via the admin page). Don't read provider keys from
  `CONFIG` or `os.environ` anywhere else.
- Add a new provider by dropping `app/llm/providers/<name>.py` with a
  `PROVIDER` instance and importing+registering it from
  `app/llm/providers/__init__.py`. Don't add if/elif branches in `client.py`.
- In tests, patch `app.llm.client.stream`/`complete` for caller-level tests,
  or the per-provider `_client` for SDK-shape tests. Never import the real
  provider SDKs in tests.

### Auth — decorators, not raw session reads

In API code, gate routes with `@login_required` or `@admin_required` from
`app.auth`. Read the active user with `current_user()`. Don't touch
`flask.session["user_id"]` outside `app/auth/`.

- Public endpoints (signup, login, `/auth/config`, inbound webhooks) are
  explicit — everything else uses `@login_required`.
- The first registered user is auto-admin (`users_repo.create` checks
  `count() == 0`). Admin can't be left at zero (see `app/api/admin.py` —
  demote/delete guard against `admin_count() <= 1`).

### Database — small repo modules, no ORM

Pattern lives in `app/auth/users.py`: a module of free functions that each
open a connection from `app.db.sqlite.connect()` in a `try/finally`. Keep
SQL inline; don't introduce SQLAlchemy.

- One repo module per logical aggregate (`users`, `documents`, `triggers`, …).
- Repos return `sqlite3.Row` for reads, primitive ids for writes. Pydantic
  models in `app/models/` are for HTTP shapes, not the DB layer.
- All schema changes go in a new file under `app/db/migrations/`. Filenames
  are lex-sorted and applied once via the `_migrations` table — never edit
  an applied migration; add a new one. Avoid destructive ALTERs; SQLite's
  ALTER is limited (no DROP COLUMN before 3.35; rebuild the table if needed).

### Wiki edits — through `app/wiki/git.py`

Never `subprocess.run(["git", ...])` from anywhere else. The wrapper enforces
working dir, identity, and commit-on-write. Path validation goes through
`app/wiki/filesystem.py:safe_rel_path` to block traversal.

- For any user/agent write to the wiki: `commit_file()`, then enqueue
  `tasks.reindex.reindex_document`. The web request shouldn't index inline.

### Background work — Huey, not threads

If something might take more than ~100ms, queue it. Tasks live under
`app/tasks/` and import `huey` from `app.tasks.huey_app`. The `worker`
container runs `python -m app.tasks.run_worker` — make sure new task modules
are imported there (or transitively) so they register on boot.

### Logging — `app.utils.logging.setup_logging` once per process

Module code uses standard `log = logging.getLogger(__name__)` and emits at
`debug/info/warning/error/exception` levels. Process entry points
(`app/main.py:create_app`, `app/tasks/run_worker.py:main`) call
`setup_logging()` exactly once to install the formatter on the root logger.

- Format: `<ts> [<level>] <logger> (<file>:<line>): <msg>` — level is
  controlled by the `LOG_LEVEL` env var (default `INFO`).
- Don't `print()` from app code; don't call `logging.basicConfig` anywhere
  outside `setup_logging`.
- Use `log.exception(...)` inside `except` blocks to capture the traceback.
- Set `LOG_LEVEL=DEBUG` to dump full LLM message history, tool definitions,
  tool calls, and tool results untruncated. Hot-path serialization is gated
  behind `log.isEnabledFor(DEBUG)`, so leaving it at INFO has no cost.

### Triggers — git-backed, SQLite is a cache

The source of truth for a trigger is its YAML file in the wiki repo, sitting
inline next to the scope it acts on:

- doc-scoped: `<dir>/.trigger_<id>_<docbase>.yaml` next to the doc
- folder-scoped: `<dir>/.trigger_<id>.yaml` inside the folder

The `triggers` SQLite row (including `file_path`) is a denormalized cache
for fast fan-out lookup and id→path resolution. When mutating triggers,
write/delete the file first via `app/triggers/storage.py`, then upsert/delete
the row, in the same task. `app/triggers/repo.py:rebuild_from_filesystem`
re-converges the cache by walking tracked `.trigger_*.yaml` paths.

### HTTP API — blueprints stay thin

Blueprints in `app/api/` parse the request, call into a domain module, and
serialize the result. Business logic lives in `app/auth/`, `app/wiki/`,
`app/triggers/`, `app/llm/agents/`. If you find yourself doing a multi-step
workflow inside a route handler, push it down.

Error responses use `{"error": "<message>"}` with the right status code (see
`app/api/auth.py`). The frontend's `ApiError` parses this shape.

## Frontend rules

### Network — only via `src/lib/api.ts:apiFetch`

`apiFetch<T>(path, init?)` sets `credentials: "include"`, JSON content type,
and parses the `{error}` envelope into `ApiError` with a `.status`. Don't
call `fetch` directly.

### Auth — only via `src/lib/auth.tsx`

Pages call `useRequireAuth()` to gate, `useAuth()` to read state. Don't
call `/api/auth/me` from a component — let the provider own that. New auth
flows (e.g. password reset) extend the context, not the pages.

### Shared chrome — `<AppShell>`

Top-level pages wrap their content in `AppShell` (in
`src/components/common/`). Navigation, the user badge, and sign-out live
there.

### Markdown

Use `react-markdown` + `remark-gfm` (already wired in the wiki page). Don't
inject HTML from the backend.

### Components

- Functional, typed props.
- Server components are fine, but anything reading auth must be `"use client"`.
- Place reusable components under `src/components/<area>/` and route-scoped
  components co-located with the route.

## Testing

We don't have a test harness wired yet — when you add tests, follow these.

### Backend

- `pytest`. The Flask app exposes `create_app()` → use Flask's `test_client`.
- Per-test isolation:
  - point `APP_DB_PATH` and `QUEUE_DB_PATH` at `tmp_path` via monkeypatch
    **before** importing `app.config`/`app.main`;
  - point `WIKI_DIR` at a tmp directory; `ensure_wiki_repo()` will init it.
- **Mock at the seam, not the SDK.** Patch `app.llm.client.complete` to return
  a canned normalized dict. Never patch `anthropic.Anthropic` directly.
- **Don't mock git.** The git wrapper is small and shelling out against a
  real tmp repo gives you real coverage. Mocking `subprocess` is brittle.
- **Don't mock SQLite.** Use a real tmp DB; migrations run on `init_db()`.
- For Huey tasks, set `huey.immediate = True` in the test fixture and call
  the task synchronously, asserting on side effects.

### Frontend

Type-check with `npm run typecheck`. Component tests can be added with Vitest
+ React Testing Library when needed. Keep components pure functions of props
so they're trivially testable.

## Adding a feature — checklist

1. New persistent state? Add a numbered migration in `app/db/migrations/`.
2. New repo functions in `app/<area>/<thing>.py` — keep them tight, return rows.
3. New domain logic next to the repo, not inside the API route.
4. Expose via a blueprint in `app/api/<thing>.py`. Gate with
   `@login_required` or `@admin_required`.
5. If the work is non-trivial or hits the LLM, queue a Huey task.
6. Add a pydantic model in `app/models/` for any non-trivial request/response.
7. Frontend: add a typed call in a `src/lib/<thing>.ts` (or extend an existing
   lib), then a page or component that uses it via `apiFetch` and `useAuth`.

## What not to do

- Don't import `anthropic`, `openai`, `google.genai`, or `ollama` outside the
  matching `app/llm/providers/<name>.py` module.
- Don't read `flask.session` outside `app/auth/`.
- Don't shell out to `git` outside `app/wiki/git.py`.
- Don't put business logic inside a Flask blueprint — push it to a domain module.
- Don't add an ORM. Direct sqlite + small repos is the pattern.
- Don't edit an already-applied migration. Add a new one.
- Don't read provider keys from `os.environ` or `CONFIG` at call time —
  go through `app/llm/settings.py:get()` so the admin UI overrides take effect.
- Don't leak raw exceptions through the API. Translate to `{error: msg}` with
  a sensible status.
- Don't make destructive CLI calls (force push, hard reset on the wiki repo)
  — every wiki commit should be additive history.

## Open questions worth knowing

- **Cost** — every connector update fans out to a doc-updater LLM pass.
  Batching/debounce isn't built yet; new ingestion paths should land behind
  a Huey task so we can add backpressure later without changing the API.
- **Doc bloat / loss** — the `document_updater` system prompt forbids both,
  but we'll need eval data. If you change the prompt, save the old version
  in git history (it already does — don't squash).
- **Permissioning** — out of scope for v0. Anything authenticated reads/writes
  everything not behind `@admin_required`.
