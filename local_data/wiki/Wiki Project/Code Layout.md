# Code Layout

What lives where, with one-line annotations on each module so you can
jump to the right file without grepping. For *why* the seams are
shaped this way, see `CLAUDE.md` ("Architectural rules — required
interfaces and seams") and the [Architecture Overview](Architecture%20Overview.md).

## Top of the repo

```
agent-wiki/
├── backend/                 Flask app, workers, tests, migrations
├── frontend/                Next.js 14 (App Router) + TypeScript
├── nginx/                   reverse proxy (prod compose only)
├── deploy/                  helm chart, terraform, custom postgres image
├── docs/                    architecture & API reference (checked-in)
├── wiki/seed/               sample content copied into a fresh wiki dir
├── local_data/wiki/         the wiki working tree itself (git-backed)
├── docker-compose.yml       full local stack
├── CLAUDE.md                rules an agent working here must follow
└── README.md
```

`local_data/` is the host-mounted scratch space for the dev stack —
its `wiki/` subdirectory is a real git repo. Everything else under
`local_data/` is gitignored.

## `backend/`

```
backend/
├── pyproject.toml           uv project; ruff + basedpyright config
├── alembic.ini
├── app/                     the actual application
├── tests/                   pytest suite (per-test Postgres schema)
└── scripts/                 (currently empty — placeholder)
```

### `backend/app/` — the application

```
backend/app/
├── main.py                  Flask app factory; blueprint registration
├── config.py                env loading, CONFIG dataclass
├── api/                     thin HTTP layer (blueprints)
├── auth/                    sessions, bcrypt, OIDC, MCP tokens, groups
├── wiki/                    git wrapper, ACL, search, edit primitives
├── triggers/                NL trigger storage + evaluation engine
├── chat/                    chat sessions persistence
├── ingest/                  ingestion settings (connector knobs)
├── llm/                     provider-agnostic LLM client + agents
├── mcp_server/              inbound MCP transport + auth + tools
├── db/                      ORM models, sessions, FTS helpers, migrations
├── models/                  pydantic request/response schemas
├── tasks/                   pgmq queues, workers, periodic scheduler
├── web/                     outbound web fetch (Firecrawl, Serper)
├── utils/                   logging setup
└── scripts/                 (currently empty)
```

#### `app/api/` — HTTP blueprints

```
app/api/
├── auth.py                  signup, login, logout, /me, /config, OIDC
├── admin.py                 admin user mgmt (last-admin guard)
├── users.py                 user listing for principal pickers
├── permissions.py           groups + ACL grants + ownership transfer
├── mcp_tokens.py            personal API token CRUD
├── mcp_connections.py       outbound MCP client config (admin)
├── mcp_server.py            inbound MCP JSON-RPC + SSE endpoint
├── documents.py             wiki list/read/write/move
├── events.py                event log
├── chat.py                  chat sessions + streaming
├── triggers.py              trigger CRUD + history
├── llm.py                   admin LLM settings
├── health.py                /health, provider check
└── webhooks.py              public connector webhooks
```

Mount points are wired in `app.main:create_app` (`/api/auth`,
`/api/admin`, etc.). Blueprints stay thin — they parse, gate, and
delegate. Business logic lives in the domain modules below.

#### `app/auth/` — identity + groups

```
app/auth/
├── __init__.py              decorators (login/admin_required, require_can)
├── basic.py                 email/password authenticate()
├── passwords.py             bcrypt hash + verify
├── users.py                 user repo (first-user-is-admin rule)
├── groups.py                group repo + membership
├── whitelist.py             ALLOWED_EMAILS signup gate
├── oidc.py                  authlib OIDC client + upsert_oidc_user
└── mcp_tokens.py            personal API token repo (bcrypt at rest)
```

See [Auth and Permissions](Specific%20Features/Auth%20and%20Permissions.md)
for the full story.

#### `app/wiki/` — the git working tree + permissions

```
app/wiki/
├── git.py                   ONLY place that shells out to git
├── filesystem.py            safe_rel_path, parent_dirs (path validation)
├── edit.py                  high-level edit primitives (used by API+agents)
├── patch.py                 line-anchored unified-diff apply
├── notify.py                post-write lifecycle hooks (ACL, search, MCP)
├── acl.py                   owner repo, ACL entries, resolver, bulk filter
├── search.py                BM25 search (delegates to db/fts.py)
├── links.py                 cross-reference resolution
└── agent_activity.py        per-doc activity attribution
```

The hard rule: *don't `subprocess.run(["git", ...])` from outside
`git.py`.* Same for raw filesystem path math — go through
`filesystem.safe_rel_path`.

#### `app/triggers/` — NL triggers

```
app/triggers/
├── storage.py               YAML files in the wiki repo (source of truth)
├── repo.py                  Postgres cache (rebuild_from_filesystem)
├── engine.py                evaluator + fan-out
├── diff.py                  delta computation between revisions
├── natural_language.py      LLM-driven match & action
├── time_based.py            scheduled (cron) triggers
└── destinations.py          outbound action targets (email, etc.)
```

YAML on disk is the truth; the `triggers` Postgres row is a
denormalized cache for fan-out lookup.

#### `app/llm/` — provider-agnostic LLM

```
app/llm/
├── client.py                stream() + complete() — the only call seam
├── settings.py              DB-backed provider/model/key resolution
├── errors.py                normalized exception types
├── providers/
│   ├── __init__.py          registry
│   ├── _common.py           shared helpers
│   ├── anthropic.py
│   ├── openai.py
│   ├── gemini.py
│   └── ollama.py
├── prompts/                 markdown system+user prompts
│   ├── chat.system.md
│   ├── document_updater.system.md
│   ├── document_updater.user.md
│   ├── wiki_qa.system.md
│   └── app_help.md
└── agents/
    ├── chat.py              the interactive chat agent
    ├── document_updater.py  doc-reconciliation agent (worker-side)
    ├── wiki_qa.py           read-only NL Q&A sub-agent
    ├── _session.py          per-turn agent state
    └── tools/               tool definitions (one .py + matching .json)
        ├── read_doc.py / .json
        ├── write_doc.py / .json
        ├── edit_doc.py / .json
        ├── multi_edit.py / .json
        ├── apply_patch.py / .json
        ├── move_path.py / .json
        ├── create_directory.py / .json
        ├── search_wiki.py / .json
        ├── list_history.py / .json
        ├── ask_nl_question.py / .json
        ├── update_doc_nl.py / .json
        ├── explain_functionality.py / .json
        ├── read_page.py / .json
        ├── open_urls.py / .json
        ├── web_search.py / .json
        ├── run_bash.py / .json (with _bash.py helper)
        ├── create_trigger.py / .json
        ├── update_trigger.py / .json
        ├── get_trigger_destinations.py / .json
        └── _doc_helpers.py
```

Each tool ships as a `.json` JSONSchema (the model sees this) plus a
`.py` handler (registered in `agents/tools/__init__.py`).

`PROVIDER` instances in `providers/<name>.py` satisfy the `Provider`
protocol; the client doesn't branch on backend.

#### `app/mcp_server/` — inbound MCP

```
app/mcp_server/
├── transport.py             JSON-RPC over POST + SSE on GET /api/mcp
├── auth.py                  bearer_required → g.user
├── session.py               per-connection state
├── tools.py                 tool registration (read_doc, edit_doc, …)
├── resources.py             wiki:///<path> resource resolution
├── pubsub.py                Postgres LISTEN/NOTIFY fan-out for SSE
├── jobs.py                  async update_doc_nl job lifecycle
└── worker_context.py        reconstitute g.user inside worker writes
```

See [MCP Server Inbound](Specific%20Features/MCP%20Server%20Inbound.md).

#### `app/db/` — schema + sessions

```
app/db/
├── models.py                ALL ORM tables (DeclarativeBase / Mapped)
├── session.py               session() context manager + init_db()
├── fts.py                   pg_textsearch BM25 query helpers (raw SQL OK)
└── migrations/
    ├── env.py               alembic env
    ├── script.py.mako
    └── versions/
        ├── 0001_initial.py              bootstrap (Base.metadata.create_all)
        ├── 0004_mcp_jobs.py             async MCP job tables
        ├── 0005_chat_sessions.py        chat sessions + messages
        └── 0006_agent_activity_cleanup_msg_id.py
```

`init_db()` runs `alembic upgrade head` on every boot. New schema
change → edit `models.py` then `cd backend && alembic revision
--autogenerate -m "<slug>"`.

#### `app/models/` — HTTP shapes (pydantic)

```
app/models/
├── _helpers.py              parse_body, error envelope
├── auth.py                  AuthSession, LoginRequest, etc.
├── user.py
├── admin.py
├── permissions.py           ACL entries, group payloads
├── document.py
├── chat.py
├── trigger.py
├── event.py
├── llm.py
├── mcp.py
└── health.py
```

These are the request/response shapes — distinct from the ORM
classes in `db/models.py`. Repos return dicts, not ORM objects, so
the API layer doesn't depend on SQLAlchemy.

#### `app/tasks/` — pgmq workers

```
app/tasks/
├── queue.py                 TaskQueue abstraction (pgmq.send/read/delete)
├── queues.py                three TaskQueue instances (documents/triggers/lightweight_maintenance)
├── run_worker.py            entry point: python -m app.tasks.run_worker <queue>
├── periodic.py              in-process cron scheduler (triggers worker)
├── document_update.py       LLM doc-updater task body
├── triggers.py              fan_out_trigger_eval + scheduled eval
├── reindex.py               BM25 reindex_document
├── chat_title.py            background chat-session title generation
└── agent_activity.py        async activity attribution write-back
```

Three queues, one worker per queue. Tasks must be imported by
`run_worker.py` so they register on boot.

#### Smaller subpackages

```
app/chat/
└── sessions.py              chat session repo (CRUD + message log)

app/ingest/
└── settings.py              connector knobs (debounce, etc.)

app/web/
├── firecrawl.py             outbound URL fetch
├── serper.py                outbound web search
├── settings.py              provider keys
└── models.py                shared response shapes

app/utils/
└── logging.py               setup_logging() — call once per process
```

### `backend/tests/`

```
backend/tests/
├── conftest.py              per-test Postgres schema, tmp WIKI_DIR, LLM mock
├── _seed.py                 shared helpers (seed_user, list_events, …)
├── test_acl.py              ACL resolver + bulk filter
├── test_auth_oidc.py
├── test_mcp_tokens.py
├── test_agent_tool_permissions.py
├── test_chat_sessions.py / test_chat_stream.py
├── test_doc_tools.py / test_dir_tools.py / test_read_page.py
├── test_wiki_edit.py / test_wiki_filesystem.py / test_wiki_links.py
├── test_wiki_agent_activity.py
├── test_documents_activity_api.py
├── test_events_api.py
├── test_explain_functionality.py
├── test_run_bash.py
├── test_web_tools.py
├── test_llm_client.py / test_llm_settings.py
├── test_bm25_indexer_e2e.py
├── test_save_to_fire_e2e.py
├── test_mcp_e2e.py
├── test_mcp_server_jobs.py / _subscriptions.py / _tools.py / _transport.py / _writes.py
├── test_triggers_api.py / _diff.py / _engine.py / _fanout.py / _natural_language.py / _repo.py
├── test_trigger_tools.py
└── integration/
    ├── conftest.py          full-stack fixture (Flask + DB + wiki + scripted LLM)
    ├── test_smoke.py
    ├── test_doc_ingest_flow.py
    ├── test_doc_tamper_flow.py
    ├── test_bm25_indexing_flow.py / test_bm25_search.py
    ├── test_folder_trigger_flow.py
    ├── test_trigger_negative_flow.py
    ├── test_frontmatter_flow.py
    ├── test_permissions_api.py / test_permissions_flow.py
    └── test_smoke.py
```

Unit tests sit at the top; `integration/` exercises the Flask client
+ real DB + real wiki repo + scripted LLM mock end-to-end.

## `frontend/`

```
frontend/
├── package.json             next 14, react-markdown, remark-gfm, swr
├── tsconfig.json
├── next.config.js           /api/* rewrite to BACKEND_URL
├── next-env.d.ts
└── src/
    ├── app/                 App Router pages (one folder = one route)
    ├── components/          reusable UI
    ├── lib/                 typed API + auth + permissions + theme
    └── types/               shared TS types
```

### `frontend/src/app/` — routes

```
src/app/
├── layout.tsx               root layout (AuthProvider, AppShell)
├── page.tsx                 landing
├── login/page.tsx
├── signup/page.tsx
├── wiki/[[...slug]]/page.tsx     the wiki viewer/editor (catch-all)
├── events/page.tsx
├── triggers/page.tsx
├── agents/page.tsx
└── admin/
    ├── page.tsx
    ├── users/page.tsx       user list, last-admin guard mirrored in UI
    ├── groups/page.tsx      group CRUD + member mgmt
    ├── llm/page.tsx         provider/model/key settings
    ├── web/page.tsx         outbound web settings
    └── health/page.tsx      provider check
```

### `frontend/src/components/` — shared UI

```
src/components/
├── common/
│   ├── AppShell.tsx              top nav, user badge, sign-out
│   └── IconComparePopup.tsx
├── chat/
│   ├── ChatWidget.tsx            the floating chat panel
│   └── ChatHistoryPanel.tsx
├── triggers/
│   ├── TriggerModal.tsx
│   └── TriggerHistoryModal.tsx
└── wiki/
    ├── WikiSearch.tsx            BM25 search palette
    ├── ShareDialog.tsx           per-page ACL UI
    └── RunAgentModal.tsx         "ask the wiki" + run-agent UI
```

### `frontend/src/lib/` — the only place pages talk to the network

```
src/lib/
├── api.ts                   apiFetch (credentials: include) + apiStream (SSE)
├── auth.tsx                 AuthProvider, useAuth, useRequireAuth
├── swr.tsx                  SWRConfig
├── theme.ts                 design tokens (colors, radii, shadows)
├── permissions.ts           groups + ACL hooks + mutations + visibility()
├── documents.ts             wiki API typings
├── triggers.ts
├── chat.ts
├── agents.ts
├── events.ts
├── llm.ts
└── health.ts
```

Hard rule: components don't `fetch()` directly and don't read auth
state from anywhere except `useAuth()`. Same applies to colors —
inline styles pull from `theme.ts`, never from raw hex.

```
src/types/
└── index.ts                 cross-feature TS shapes
```

## Non-app directories

```
deploy/
├── helm/agent-workspace/    chart for k8s deployment
├── terraform/               example tfvars + main.tf for cloud bring-up
├── postgres/Dockerfile      custom Postgres 17 image (pg_textsearch + pgmq)
└── README.md

nginx/
├── Dockerfile
└── nginx.conf               /api/* → backend, everything else → frontend

wiki/
└── seed/                    sample content copied into fresh local_data/wiki/
    ├── README.md
    └── projects/

docs/
├── architecture.md          container/volume/data-flow reference
├── api.md                   HTTP API surface
└── mcp-server.md            inbound MCP design

local_data/
└── wiki/                    the wiki working tree (git-backed)
                             — every .md you see in the UI lives here
```

## Where the rules live

If you're adding a feature and aren't sure which seam to use, consult
in this order:

1. **`CLAUDE.md`** — the architectural seams and "what not to do"
   list. Most surprises are answered here.
2. **[Architecture Overview](Architecture%20Overview.md)** — the big
   picture and a typical read/write flow.
3. **[Running Locally](Running%20Locally.md)** — five-process dev
   loop and debugging recipes.
4. **`docs/architecture.md` / `docs/api.md`** — repo-checked-in
   reference docs (containers, volumes, request shapes).
