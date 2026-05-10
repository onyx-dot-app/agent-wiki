# Architecture Overview

A bird's-eye view of agent-wiki — what the moving parts are and how a
request or background task flows through them. For deeper dives, follow
the links at the bottom.

## The picture

```
                          ┌──────────────┐
                          │   Browser    │
                          └──────┬───────┘
                                 │  :80
                          ┌──────▼───────┐
                          │    nginx     │   reverse proxy
                          └──┬────────┬──┘
                  /api/*     │        │   everything else
                             │        │
                  ┌──────────▼─┐  ┌───▼─────────┐
                  │  backend   │  │  frontend   │
                  │  Flask     │  │  Next.js 14 │
                  │  :8080     │  │  :3000      │
                  └────┬───┬───┘  └─────────────┘
                       │   │
       ┌───────────────┘   └──────────────┐
       │                                  │
┌──────▼─────────┐               ┌────────▼───────────────┐
│  wiki-data     │               │   Postgres 17          │
│  (git repo)    │               │   ─ app state (ORM)    │
│  ─ pages.md    │               │   ─ pg_textsearch BM25 │
│  ─ trigger     │               │   ─ pgmq queues:       │
│    YAML        │               │     • documents        │
│  shelled out   │               │     • triggers         │
│  to git        │               │     • lightweight_     │
│                │               │       maintenance      │
└────────────────┘               └────────┬───────────────┘
       ▲                                  │
       │  commit_file                     │  pgmq.read / send
       │                                  │
       │                          ┌───────┴────────┐
       │                          │                │
┌──────┴──────┐  ┌──────────┐  ┌──▼────────┐  ┌────▼────────┐
│ documents   │  │ triggers │  │ lw-maint  │  │   LLM       │
│ worker      │  │ worker   │  │ worker    │  │  providers  │
│ (LLM doc-   │  │ (NL eval │  │ (BM25 +   │  │  Anthropic  │
│  updater)   │  │  + cron) │  │  expiry)  │  │  OpenAI     │
└─────┬───────┘  └──────────┘  └───────────┘  │  Gemini     │
      │                                       │  Ollama     │
      └───────────────────────────────────────►             │
              app/llm/client.py (single seam) │             │
                                              └─────────────┘
```

## What each piece does

**nginx** — reverse proxy on `:80`. `/api/*` → Flask, everything else →
Next.js. In local dev (no nginx) the Next dev server's rewrite plays
the same role.

**backend (Flask, :8080)** — the API. Blueprints under `app/api/` stay
thin: parse, gate via `@login_required` / `@admin_required` /
`require_can`, delegate to a domain module
(`app/auth/`, `app/wiki/`, `app/triggers/`, `app/llm/agents/`),
serialize. Anything slow (LLM calls, reindex, trigger fan-out) is
pushed to a pgmq queue.

**frontend (Next.js 14, :3000)** — App Router + TS. All network calls
go through `src/lib/api.ts:apiFetch`; auth state lives in
`src/lib/auth.tsx`. Inline-styled components pull tokens from
`src/lib/theme.ts` (no Tailwind / CSS-in-JS).

**Postgres 17** — single store for app state *and* the task queue.
The schema (SQLAlchemy 2.0 ORM in `app/db/models.py`) is applied by
Alembic on every boot. Two extensions do the heavy lifting:
`pg_textsearch` powers BM25 search over `documents_fts`, and `pgmq`
hosts the three task queues (`pgmq.q_documents`, `pgmq.q_triggers`,
`pgmq.q_lightweight_maintenance`).

**wiki-data volume** — a real git working tree. Every page edit is a
commit; trigger definitions are YAML files alongside the page or
folder they scope. The backend shells out to git **only** through
`app/wiki/git.py`. Untracked files are invisible to the API
(listings come from `git ls-files`).

**Workers** — three long-lived processes, one per pgmq queue.
- *documents* runs the LLM document-updater agent on incoming
  connector payloads.
- *triggers* evaluates NL triggers (delta + scheduled cron, in-process).
- *lightweight_maintenance* runs sub-second upkeep — keeps the search
  index fresh after every commit, and runs the delayed
  agent-activity expiration cleanups.

Each queue has its own worker so a slow LLM pass can't block search
reindex or trigger fan-out. Skipping a worker doesn't crash the app —
its messages just back up.

**LLM seam** — every model call goes through `app/llm/client.py`
(`stream` / `complete`). Provider modules live under
`app/llm/providers/`, registered from `__init__.py`. Provider, model,
and credentials come from the DB-backed `app/llm/settings.py:get()`
(configured via Admin → LLM), not env vars. Adding a provider is
dropping a file with a `PROVIDER` instance — no `if`/`elif` in the
client.

## A typical write

1. Connector posts to `/api/documents/ingest` (or a webhook).
2. Backend records an `events` row, enqueues
   `update_document_from_payload` on `pgmq.q_documents`, returns 202.
3. *documents* worker runs the document-updater agent; if the body
   changes, calls `app.wiki.git.commit_file`.
4. The post-commit lifecycle hook enqueues `reindex_document` on
   `pgmq.q_lightweight_maintenance` and `fan_out_trigger_eval` on
   `pgmq.q_triggers`.
5. *lightweight_maintenance* worker rebuilds the FTS row. *triggers* worker matches
   delta triggers scoped to the doc and each parent directory and
   records `trigger.fire` events.

## A typical read

1. Browser hits `/wiki/<path>`; `apiFetch` calls
   `/api/documents/<path>`.
2. Blueprint runs `@login_required` then
   `require_can("read", path)` (ACL check via `app/wiki/acl.py`).
3. Domain layer reads via `git show HEAD:<path>`; response goes back
   as markdown, rendered with `react-markdown` + `remark-gfm`.
4. Search (`/api/wiki/search`) hits the BM25 index in Postgres,
   filtered through `acl.visible_paths_filter` so users never see
   paths they can't read.

## Architectural seams worth knowing

These are the boundaries the codebase enforces — honor them and the
system stays swappable.

| Seam | What it's for |
|---|---|
| `app/llm/client.py` | only allowed entry point for LLM calls |
| `app/wiki/git.py` | only place that shells out to git |
| `app/wiki/acl.py` + `require_can` | per-page authorization |
| `app/db/session.py:session()` | ORM session lifecycle (commit/rollback) |
| `app/tasks/queue.py` + `queues.py` | pgmq queue abstraction & routing |
| `src/lib/api.ts:apiFetch` | only allowed network call from the UI |
| `src/lib/auth.tsx` | auth context (no raw `/api/auth/me` calls) |
| `src/lib/theme.ts` | design tokens (no raw hex / radius integers) |

## Where to go next

- [Running Locally](Wiki%20Project/Running%20Locally.md) — five-process
  dev loop, VS Code launch configs, debugging recipes.
- [MCP Server Inbound](Wiki%20Project/Specific%20Features/MCP%20Server%20Inbound.md)
  — exposing wiki content over MCP.
- `docs/architecture.md` — container/volume/data-flow reference shipped
  with the repo.
- `docs/api.md` — HTTP API surface.
- `CLAUDE.md` — the rules an agent working in this codebase must follow.
