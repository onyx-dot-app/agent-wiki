# Architecture Diagram (as built)

Snapshot of what exists in the repo today. Companion to
`architecture_and_progress.md` — that file owns intent and decisions; this one
just shows the wiring.

_Last updated: 2026-05-07_

---

## 1. Process / container shape

```
                       ┌──────────────┐
                       │   browser    │
                       └──────┬───────┘
                              │
                              ▼
                       ┌──────────────┐
                       │ nginx :80    │  (compose path; in host dev,
                       │              │   Next rewrite proxies /api/*)
                       └──┬────────┬──┘
                /api/*    │        │   everything else
                          ▼        ▼
                 ┌──────────────┐  ┌──────────────────────┐
                 │ backend :8080│  │ frontend :3000       │
                 │ Flask        │  │ Next.js 14 App Router│
                 │ (sessions)   │  │ "use client" pages   │
                 └──┬───────────┘  └──────────────────────┘
                    │ enqueue
                    ▼
       ┌─────────────────────────┐
       │ queue.sqlite  (Huey)    │◀──────┐
       └─────────────────────────┘       │ pulls jobs
                    ▲                    │
                    │ writes             │
       ┌────────────┴────────────┐   ┌───┴────────────────┐
       │ app.sqlite              │   │ worker container   │
       │ users / documents /     │◀──│ python -m          │
       │ triggers / events /     │   │ app.tasks.run_     │
       │ documents_fts (FTS5) /  │   │ worker             │
       │ llm_settings /          │   └───┬────────────────┘
       │ mcp_connections /       │       │ git ops + reindex
       │ _migrations             │       ▼
       └─────────────────────────┘   ┌───────────────────────┐
                    ▲                │ wiki working tree     │
                    │ shared volume  │ (git-backed,          │
                    └────────────────│  volume: wiki-data)   │
                                     └───────────────────────┘
```

Two SQLite files (`app.sqlite`, `queue.sqlite`) on volume `app-data`; the
wiki working tree on volume `wiki-data`. The backend and worker share both.

---

## 2. Backend layout (`backend/app/`)

```
api/             ── thin Flask blueprints, no business logic
  auth.py            POST /signup, /login, /logout; GET /auth/me, /auth/config
  admin.py           users CRUD + LLM settings (admin-only)
  users.py           current-user reads
  documents.py       wiki list / read / write / search / reindex
  triggers.py        per-user CRUD
  events.py          reverse-chronological event feed
  chat.py            POST /chat/messages (SSE stream)
  webhooks.py        inbound webhook stub
  mcp.py             MCP connections stub

auth/            ── @login_required, @admin_required, current_user()
                    bcrypt + flask session; first user auto-admin

db/
  sqlite.py          connect()
  fts.py             FTS5 helpers (porter+unicode61, bm25)
  migrations/        numbered .sql, applied once via _migrations table

llm/
  client.py          ★ single seam: complete() + stream()
                       Anthropic via messages.stream
                       OpenAI via Responses API (responses.create stream=True)
  settings.py        DB-backed provider/model/keys (env is fallback only)
  agents/            chat loop, document_updater, trigger evaluator
  prompts/           system+user prompt strings

triggers/
  repo.py            SQLite repo (source of truth in v0)
  engine.py          SQL match + NL eval orchestration
  natural_language.py LLM-backed match verdict
  diff.py            change-payload shaping
  time_based.py      time-based check stub
  storage.py         dead — YAML/git path was dropped 2026-05-06

wiki/
  git.py             ★ only place that shells out to git
  filesystem.py      safe_rel_path, traversal guard
  search.py          FTS5 query wrapper

tasks/             ── 3 Huey queues on queue.sqlite — see background-tasks/
  huey_app.py        documents_huey / triggers_huey / wiki_doc_index_huey
  run_worker.py      worker entrypoint; takes <queue> arg
  reindex.py         reindex_path / reindex_document  → wiki_doc_index
  triggers.py        fan_out_trigger_eval             → triggers
  document_update.py doc-updater tasks                → documents
  periodic.py        crons split across triggers + documents queues

models/            ── pydantic request/response shapes (HTTP only, not DB)
utils/
config.py          ── dotenv-loaded; data paths + BACKEND_URL
main.py            ── create_app(), Flask entrypoint
```

Star (★) = enforced architectural seam — see CLAUDE.md "what not to do".

---

## 3. Frontend layout (`frontend/src/`)

```
app/                Next.js App Router pages
  page.tsx              redirects to /wiki
  layout.tsx            root layout (mounts global <ChatWidget>)
  login/                sign-in
  signup/               sign-up (whitelist-gated if ALLOWED_EMAILS set)
  wiki/                 file tree + reader (editor TBD)
  triggers/             user's triggers list + create modal
  events/               (route exists, view stub)
  admin/                admin/, admin/users, admin/llm

components/
  common/AppShell       sidebar + user badge + sign-out chrome
  wiki/                 tree view, reader, "+ Trigger" button
  triggers/TriggerModal reusable create/edit modal
  chat/ChatWidget       global FAB → bottom-right widget → resizable right panel (pushes page left); SSE streaming

lib/
  api.ts                ★ apiFetch<T> + apiStream (SSE) — only network seam
  auth.tsx              ★ AuthProvider, useAuth, useRequireAuth — only auth seam
  triggers.ts           typed trigger calls
  events.ts             typed events calls

types/                  shared TS types
```

---

## 4. Key request flows that work today

### Login / session
```
browser ── POST /api/auth/login ──▶ api/auth.py
                                    ├─ users_repo.get_by_email
                                    ├─ bcrypt.checkpw
                                    └─ flask.session["user_id"] = id
                                    ◀── { user }
```

### Read a wiki file
```
browser ── GET /api/documents/file?path=… ──▶ api/documents.py
                                              ├─ safe_rel_path
                                              ├─ wiki/git.py read
                                              └─ return body
```

### Save a wiki file (and fan out triggers)
```
browser ── PUT /api/documents/file ──▶ api/documents.py
                                       ├─ safe_rel_path
                                       ├─ wiki/git.py:commit_file
                                       ├─ enqueue tasks.reindex.reindex_path
                                       └─ enqueue tasks.triggers.fan_out_trigger_eval
                                                       │
                          worker ◀────────────────────┘
                          ├─ FTS reindex
                          └─ for each matching trigger:
                             ├─ llm.client.complete (NL eval)
                             └─ on match → events row (kind=trigger.fire)
```

### Chat (streaming)
```
browser ── POST /api/chat/messages (JSON) ──▶ api/chat.py
                                              └─ run_chat_loop_stream
                                                  └─ llm/client.py:stream
                                                      ├─ Anthropic messages.stream
                                                      └─ OpenAI responses.create(stream=True)
       ◀── SSE: text_delta… tool_call… done
```

### Trigger CRUD
```
browser ── /api/triggers (GET/POST/PATCH/DELETE) ──▶ api/triggers.py
                                                     └─ triggers/repo.py (SQLite only)
```

---

## 5. What's wired vs. stubbed

| Slice                                | State    |
|---|---|
| Auth (signup, login, sessions)       | live     |
| Admin: users, LLM settings           | live     |
| Wiki: list / read                    | live     |
| Wiki: write (PUT) + commit           | live (no UI editor yet) |
| FTS index + manual reindex button    | live     |
| Auto-reindex on commit               | live (via task) |
| Chat: stateless SSE endpoint + loop  | live; tools off; no persistence |
| Chat: global widget (FAB / bottom-right / resizable right panel that pushes page) | live |
| Chat: location ctx + propose-and-apply UX | not built |
| Triggers: CRUD API + repo            | live (SQLite-only)     |
| Triggers: Triggers tab + create modal| live     |
| Triggers: fire-path on human edits   | live     |
| Triggers: fire-path on agent edits   | not wired |
| Triggers: time-based                 | stub     |
| Events: write surface (trigger.fire) | live     |
| Events: view                         | route exists, UI stub |
| Document-updater agent               | prompts written, agent stub |
| Onyx-side push integration           | endpoint stub only |
| MCP connections                      | stub     |
| Webhooks (outbound dispatch)         | not in v0 |
| `agents.md` directory-context file   | deferred (do not implement) |

---

## 6. Data model (applied schema)

| Table             | Purpose                                            |
|---|---|
| `users`           | id, email, name, password_hash, is_admin, created_at |
| `mcp_connections` | per-user MCP server entries (stub feature)         |
| `documents`       | metadata only — body lives in git                  |
| `documents_fts`   | FTS5 virtual table (porter+unicode61, bm25)        |
| `triggers`        | per-user; SQLite is the source of truth in v0      |
| `events`          | append-only audit log (trigger fires today)        |
| `llm_settings`    | single-row provider/model/keys                     |
| `_migrations`     | applied filenames                                  |

`triggers.action_json` is reserved but always `'{}'` — no v0 dispatch.
