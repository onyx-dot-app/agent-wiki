# Repo Layout

A map of what lives where in `agent-wiki`, and the logic behind the
split. Pair this with `architecture_and_progress.md` (product/architecture
intent) and `CLAUDE.md` (durable rules). This file is the
"where-does-X-live" cheat sheet.

## Top-level

```
agent-workspace/
├── backend/         Flask API + Huey workers (Python)
├── frontend/        Next.js 14 App Router UI (TypeScript)
├── nginx/           Reverse proxy: /api/* → backend, else → frontend
├── deploy/          Deployment glue (currently empty placeholder)
├── docs/            Architecture + API reference (developer-facing)
├── wiki/seed/       Sample content copied into a fresh wiki working tree
├── local_data/      Dev-only state: live wiki working tree + sqlite dbs
├── docker-compose.yml
├── .env.example     SECRET_KEY, ALLOWED_EMAILS, etc.
├── CLAUDE.md        Durable rulebook for agents working on this repo
├── LICENSE
└── README.md
```

The big idea: three runtime services (backend, frontend, nginx) plus two
data volumes (`app-data` for sqlite, `wiki-data` for the git-backed wiki
tree). Everything else is source, docs, or dev scaffolding.

## Backend — `backend/app/`

Layered by responsibility. HTTP at the edge, domain logic in the middle,
storage and integrations at the bottom. Each layer only depends downward.

```
backend/app/
├── main.py          Flask app factory: blueprint registration, init_db
├── config.py        Env-var config (paths, defaults, fallback API keys)
│
├── api/             HTTP edge — Flask blueprints, thin handlers only
│   ├── auth.py            login/signup/logout/me
│   ├── users.py           current-user endpoints
│   ├── admin.py           admin-gated user + LLM settings CRUD
│   ├── documents.py       wiki read/write/list
│   ├── triggers.py        trigger CRUD
│   ├── events.py          trigger-fire history
│   ├── chat.py            streaming chat endpoint
│   ├── webhooks.py        inbound (public) ingestion hooks
│   └── mcp.py             MCP-style tool surface
│
├── auth/            Sessions, bcrypt, whitelist, admin flag
│   ├── basic.py           @login_required / @admin_required, current_user()
│   ├── oidc.py            OIDC flow (optional)
│   ├── passwords.py       bcrypt hashing
│   ├── users.py           users repo (sqlite)
│   └── whitelist.py       ALLOWED_EMAILS gate
│
├── db/              SQLite + FTS5 + numbered migrations
│   ├── sqlite.py          connect(), init_db(), migration runner
│   ├── fts.py             FTS5 helpers
│   └── migrations/        0001_init.sql, 0002_*.sql, …  (lex-sorted, applied once)
│
├── llm/             The ONLY place we talk to model providers
│   ├── client.py          complete() — provider-agnostic entry point
│   ├── settings.py        DB-backed provider/model/key config (env fallback)
│   ├── prompts/           *.system.md / *.user.md — versioned in git
│   └── agents/            Higher-level agents that call complete()
│       ├── chat.py            chat agent (tool-using)
│       ├── document_updater.py doc-rewrite agent (fan-out target)
│       └── tools.py           tool definitions exposed to agents
│
├── wiki/            Git-backed wiki working tree
│   ├── git.py             ONLY module allowed to shell out to git
│   ├── filesystem.py      safe_rel_path() — traversal guard
│   └── search.py          FTS-backed search over docs
│
├── triggers/        Natural-language triggers (YAML in git + sqlite cache)
│   ├── storage.py         YAML <-> .triggers/*.yaml on disk
│   ├── repo.py            sqlite cache repo
│   ├── engine.py          evaluation pipeline
│   ├── diff.py            doc-diff extraction for prompts
│   ├── natural_language.py NL-condition matcher (LLM-backed)
│   └── time_based.py      cron-like trigger scheduling
│
├── tasks/           Huey background work (separate worker container)
│   ├── huey_app.py        Huey instance (sqlite broker)
│   ├── run_worker.py      worker entry point — must import all task modules
│   ├── reindex.py         FTS reindex after a wiki commit
│   ├── document_update.py LLM-driven doc rewrites
│   ├── triggers.py        trigger fan-out + evaluation
│   └── periodic.py        scheduled jobs (time-based triggers)
│
├── models/          Pydantic — request/response shapes only (NOT the DB layer)
│   ├── user.py
│   ├── document.py
│   ├── trigger.py
│   └── event.py
│
└── utils/           (currently empty — for genuinely cross-cutting helpers)

backend/tests/       pytest — uses tmp sqlite + tmp wiki repo, patches
                     app.llm.client.complete at the seam
backend/scripts/     One-off ops scripts
backend/Dockerfile
backend/pyproject.toml
```

### Why this shape
- **`api/` is thin.** Blueprints parse + serialize. Multi-step workflows
  live in the matching domain module (`auth/`, `wiki/`, `triggers/`, `llm/agents/`).
- **One repo module per aggregate.** Free functions, raw SQL, sqlite.Row
  back. No ORM. Pattern: `backend/app/auth/users.py`.
- **Single seams for risky I/O.** `llm/client.py` for models, `wiki/git.py`
  for git, `auth/basic.py` for sessions. Tests patch these — never the SDK.
- **Heavy work goes through Huey.** Anything that hits an LLM, touches FTS,
  or might exceed ~100ms enqueues a task in `tasks/`. The web request
  returns fast.
- **Triggers are git-first.** YAML on disk is source of truth; the sqlite
  row is a cache for fan-out lookup.

## Frontend — `frontend/src/`

Next.js App Router. Pages are routes; shared logic is hoisted to `lib/`
and `components/`.

```
frontend/src/
├── app/                  App Router routes
│   ├── layout.tsx              root layout (auth provider + global ChatWidget)
│   ├── page.tsx                landing
│   ├── login/  signup/         auth pages
│   ├── wiki/[[...slug]]/       file-tree + reader/editor (catch-all)
│   ├── triggers/               current user's triggers
│   ├── events/                 trigger-fire history
│   └── admin/
│       ├── llm/                provider/model/key config
│       └── users/              user CRUD
│
├── components/
│   ├── common/AppShell.tsx     nav + user badge + sign-out (top-level chrome)
│   ├── wiki/RunAgentModal.tsx
│   ├── triggers/TriggerModal.tsx
│   └── chat/ChatWidget.tsx     global FAB / bottom-right widget / right-side resizable panel (pushes page left)
│
├── lib/                  The ONLY place pages talk to network/auth
│   ├── api.ts                  apiFetch<T>() — credentials, JSON, ApiError envelope
│   ├── auth.tsx                AuthProvider, useAuth(), useRequireAuth()
│   ├── triggers.ts             typed trigger API calls
│   └── events.ts               typed event API calls
│
└── types/index.ts        Shared TS types
```

### Why this shape
- **Single network seam (`lib/api.ts`).** No raw `fetch` in components.
- **Single auth seam (`lib/auth.tsx`).** Pages gate with `useRequireAuth()`,
  read state with `useAuth()`. Components never call `/api/auth/me`.
- **`<AppShell>` owns chrome** so individual pages stay focused on content.
- **Co-locate route-only UI.** Reusable widgets live under
  `components/<area>/`; one-shot pieces stay next to their route.

## Nginx — `nginx/`

`nginx.conf` + a small Dockerfile. Routes `/api/*` to backend, everything
else to the Next dev/static server. Single ingress, single port (8080).

## Docs — `docs/`

Developer-facing reference, distinct from the in-app wiki.

- `architecture.md` — data flows + service diagram
- `api.md` — HTTP API reference

## Wiki content

Two trees, easy to confuse:

- `wiki/seed/` — checked into the repo. **Initial content** copied into a
  fresh wiki working tree on first boot. Edit here to change what new
  installs see.
- `local_data/wiki/` — **the live wiki working tree** in dev (mounted as
  the `wiki-data` volume in compose). This is a real git repo; every save
  from the app is a commit. Includes `architecture_and_progress.md` and
  the per-area design docs that agents read on every session.

## Local data — `local_data/`

Dev-only. Holds `app.sqlite`, `queue.sqlite`, and the live wiki tree. Not
committed (gitignored). Wiped by deleting the directory.

## Where to add a new feature

Mirrors the checklist in `CLAUDE.md`:

1. Schema change → new file in `backend/app/db/migrations/`.
2. Storage helpers → `backend/app/<area>/<thing>.py` (repo module).
3. Domain logic → next to the repo, NOT inside the route.
4. HTTP surface → blueprint in `backend/app/api/<thing>.py`, gated.
5. Slow / LLM work → Huey task in `backend/app/tasks/`.
6. Wire shapes → `backend/app/models/<thing>.py` (pydantic).
7. UI → typed call in `frontend/src/lib/<thing>.ts`, page/component on top.
