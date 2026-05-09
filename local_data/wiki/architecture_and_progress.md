# Architecture & Progress

> Working notebook for agent-wiki. Lives at
> `local_data/wiki/architecture_and_progress.md` — checked into the repo, and
> also a real file inside the dev wiki working tree so the running app
> renders it. Update it whenever a product, architecture, or progress
> decision is made — Claude reads this on every session.

_Last updated: 2026-05-08_

### Per-area docs (each owns its own design + progress)
- [Running locally — agent runbook](running-locally.md)
- [Flask + basic APIs](flask-and-apis/flask-and-apis.md)
- [Agent harness — document updater](agents/document-updater.md)
- [Agent harness — chat agent](agents/chat-agent.md)
- [Agent tools (every tool, sync/async, side effects)](agent-tools/agent-tools.md)
- [Natural-language triggers](natural-language-triggers/natural-language-triggers.md)
- [Frontend (ChatUI + Wiki UI + Events + Admin)](frontend/frontend.md)
- [Onyx-side push integration](onyx-push/onyx-push.md)
- [Background tasks (pgmq)](background-tasks/background-tasks.md)
- [Exploration work](exploration/exploration.md)
- [MCP server (inbound)](mcp-server/mcp-server.md)
- [Infra](infra/infra.md)
- [Permissions (proposal)](permissions/permissions.md)

This file is the cross-area map: product spec, V0 brief, cross-cutting
architecture and decisions, and a one-line-per-area status snapshot. When
working in a single area, prefer that area's doc — it carries the
file-by-file detail, the area-specific design, and the work breakdown.

---

## 1. Product / UX

### North star
A self-updating, agent-first wiki. Humans and agents collaborate on living
docs; natural-language triggers fire on changes so people stay aware of what
matters and downstream agents/services can react.

### Persona priority for v0 dogfooding
_Note only — does not affect implementation. Build for both eng and GTM use._

### App shell — three primary views + a chat side panel

Left-sidebar **tabs** (top-level views):
1. **Wiki** _(default)_ — file-system view; the entry page when you log in.
2. **Triggers** — the current user's triggers (owner-only — see Triggers UX).
3. **Events** — history of trigger fires the current user owns.

**Chat side panel.** Not a tab. A collapsible panel available on every page —
toggle open/closed from the chrome, state persists across navigation. The
panel **knows the current location** in the wiki (the doc path or directory
the user is viewing) and forwards it with each message so the agent can
answer "what's here?" and scope its proposals correctly. The panel can
**modify the page or create triggers, gated on user acknowledgement** —
edits and trigger creates show up in-thread as a diff / preview that the
user has to **Apply** (or Reject) before it lands.

Plus: settings/admin (basic CRUD for users etc.) accessible from the chrome.

### Wiki view

Renders the wiki repo as a **file tree** of directories and `.md` files,
arbitrarily named.

- **Click a directory** → opens a directory page that lists its children
  (subdirs + `.md` files). The directory page also exposes the directory's
  triggers (see below).
- **Click an `.md` file** → opens a **reader** page that renders the markdown.
- **Reader → editor toggle**: from the reader, switch to an **editor** that
  shows the raw markdown (not rendered). Editor has **Save** and **Cancel**
  buttons. Save commits to git; Cancel discards.
- **Back to file-system view** from any page.
- **`agents.md` / `agent.md` — TBD, do not implement yet.** The intent is
  that this file (when present in a directory) acts as authoring/context
  guidance for how docs in that directory should be updated, with special
  highlighting in the tree. **Agents working on this codebase should ignore
  this feature for now and not start any work on it** — design questions
  (canonical filename, recursive vs. dir-only scope, multiple-files allowed)
  are unresolved.

### Triggers UX

Triggers are **per-user** in v0. Every trigger has an owner; only the
owner sees it, edits it, and receives the events from its fires. Two
users with the same NL description on the same path are independent
triggers, each producing their own events. **Sharing/collaboration is
backlog** (see
[natural-language-triggers](natural-language-triggers/natural-language-triggers.md)).

Two surfaces for managing your own triggers:
- **Top-level Triggers tab** — every trigger you own, across the whole
  wiki. Full CRUD here; this is the "all my triggers" view.
- **Inline on doc and directory pages** — a panel that shows **your**
  triggers scoped to this path (file-scoped on the doc page;
  directory-scoped on the dir page). Add / edit / delete inline.

A trigger has:
- **scope** — a doc path or a directory path.
- **natural-language description** — when it should fire (e.g. "when this
  project's status changes from green to yellow").

**Fire conditions:**
- File-scoped trigger: the file's content changes.
- Directory-scoped trigger: any file in the directory changes **or** a new
  file is added.

The trigger fires when the scoped path changes regardless of **who** made
the edit. Visibility and the resulting event are gated on ownership: only
the trigger's owner sees the event in their Events tab.

**What firing does (v0 only):** evaluate the NL description against the
change with an LLM; if it matches, **record an event** owned by the
trigger's user.

**Trigger extensions — TBD, do not implement yet.** Anything beyond
"evaluate + record event" (outbound webhooks, HTTP calls, agent-message
dispatch, ambient UI surfacing like badges/toasts, etc.) is out of scope
for v0. **Agents working on this codebase should not implement anything
past what is described in this section.**

### Chat side panel

The chat experience is a **collapsible side panel** on every page — not a
top-level tab.

- **Toggle from the chrome.** Open/closed state persists across navigation.
- **Location-aware.** The panel knows the user's current location in the
  wiki (the doc path or directory path being viewed) and includes it with
  each message to the backend. The agent uses it to answer "what's here?"
  and to scope edits / new triggers.
- **Multi-turn LLM loop with tools.** v0 tool surface:
  - `search_wiki(query)` — bm25 over the BM25 index. _(read-only; no ack
    needed)_
  - `propose_doc_edit(path, body, message?)` — emits a draft diff into the
    chat thread. **Does not write.** The user clicks **Apply** to commit
    or **Reject** to discard. The agent sees the result on the next turn.
  - `propose_create_trigger(scope_path, kind, nl_description)` — emits a
    trigger preview into the chat thread. **Does not create.** On
    **Apply**, a trigger is created **owned by the current user**.
  - _(Owner-scoped reads — `list_my_triggers`, `read_doc` — don't need
    acknowledgement.)_
- **Acknowledgement is the contract.** Anything that writes to the wiki or
  creates a trigger goes through the propose-and-confirm flow. No silent
  writes from the chat agent.

The stateless `POST /api/chat/messages` endpoint is wired today; the
location field, the propose-and-apply tools, and persistence are
follow-ups (see [agents/chat-agent.md](agents/chat-agent.md)).

### Events view

Reverse-chronological list of trigger fires. Each entry shows:
- which trigger fired,
- what change triggered it,
- the LLM's match verdict + brief reason,
- timestamp.

### Settings / admin (basic CRUD)
- Users (admin only): list, promote/demote, delete.
- LLM provider/keys (admin only).
- Self-serve: signup (gated by `ALLOWED_EMAILS` whitelist if set), sign-out,
  password change _(future)_.

### Open UX questions
- Conflict handling when two users edit the same doc? _(probably out of v0)_
- Editor: do we want any minimal UX assists — auto-save draft on the client,
  unsaved-changes warning on navigate-away? _(yes, navigate-away warning is cheap)_
- Chat: persistent conversations across sessions, or session-only? _(default:
  persistent; multiple convos.)_

_(`agents.md` scoping/filename and trigger ambient-surfacing questions are
deferred — see the marked TBD callouts above. Don't pull them back in
without an explicit decision.)_

---

## 2. V0 specification (original brief)

Reproduced verbatim from the original product brief. **This section is the
durable reference for what V0 is meant to be**; later sections describe what's
actually built and what's left to do.

### Architecture

- **Backend container**
  - Flask web framework
  - SQLAlchemy 2.0 ORM against Postgres 17 (with `pg_textsearch` BM25
    for search)
  - One volume — the wiki working tree (git-backed). DB state lives in
    Postgres.
  - Flask shells out to git via subprocess
- **Queue**
  - pgmq (Postgres-native — `pgmq.q_<name>` tables in the same DB)
  - One queue per logical lane: `documents_queue`, `triggers_queue`,
    `wiki_bm25_queue`
  - Runs the document updates
- **Frontend container**
  - TypeScript, same stack as Onyx — can reuse a lot of the components, but
    can copy them over for now for simplicity
- **Nginx container**

### Features

- **Auth** — Basic Auth / OIDC only, no groups, no RBAC
- **APIs**
  - Auth / users
  - Managing MCP connections
  - Way of ingesting the triggers
  - Audit log of events with time-based filters, pagination, etc.
  - Webhooks for events
  - Send a call to external service
  - APIs for taking document updates:
    - take a document id for agents updating it
    - take generic updates from connector updates
    - queue reindexing work for updated docs
  - APIs for getting document / trigger history
  - Triggers also git-backed
  - Sync LLM APIs for helping users draft plans and docs
- **Background**
  - Take document update tasks and reindex them
  - Time-based checks
- **LLM items**
  - Agent harness logic for updating docs based on the APIs — this is
    probably the trickiest part
  - V0 can just be an LLM comparison on every doc; worry about scaling later
  - Watch the cost
  - Don't bloat the documents over time or throw out important stuff on
    updates
  - On saves, save the docs to git history
  - Natural-language triggers on document deltas and time-based checks on docs
  - Run checks on the directories above the file that changed
  - Chat functionality that can reference / update the docs — what tool
    interfaces should exist for this?
  - Send descriptive, actionable items to potential downstream agents (Craft)
- **ChatUI**
  - Interact with the wiki and triggers
  - Answer questions about the wiki with a search
  - Fetch / modify triggers owned by the user
  - Prefer a loop that can run multiple iterations
- **Exploration work**
  - How to get agents (coding agents for now) to reliably update the docs
    instead of too often or never. Is this just an MCP description? Should
    it be a skill?
  - Onyx-side changes to push all document changes to this system; only
    public connectors for now
- **Infra**
  - Deployment
  - Git-backed file system volume

---

## 3. Architecture (high-level)

### Process / container shape

```
[browser]
   |
   v
[nginx :80]  --/api/*-->  [backend :8080]    Flask, sessions
                                |
                                v
                         [Postgres 17 + pg_textsearch + pgmq]
                                              users / docs metadata / triggers cache /
                                              events / documents_fts (BM25) /
                                              llm_settings / pgmq.q_*
                                |
                                v
                         [worker container × 3]   python -m app.tasks.run_worker <queue>
                                |
                                v
                         [git wiki working tree]  ←--volume mount: wiki-data
   |
   ^
   '--/-->  [frontend :3000]   Next.js 14 App Router, "use client" pages
```

### Local dev — running on the host (no Docker)

The compose path is canonical, but day-to-day iteration runs the three
processes directly on the host so we skip image rebuilds. `.env` at the repo
root already points the data paths at `local_data/` and sets
`BACKEND_URL=http://localhost:8080` for the Next dev-server proxy.

- **Backend** — `cd backend && ./.venv/bin/python -m app.main` (Flask on
  `:8080`). Python 3.11 venv at `backend/.venv`, deps installed via
  `pip install -e .`.
- **Workers** — three queues, three processes. Each drains one queue:
  `./.venv/bin/python -m app.tasks.run_worker documents`,
  `./.venv/bin/python -m app.tasks.run_worker triggers`,
  `./.venv/bin/python -m app.tasks.run_worker wiki_bm25`
  (same venv). See
  [running-locally.md](running-locally.md#how-to-run--five-processes)
  and [background-tasks](background-tasks/background-tasks.md).
- **Frontend** — `cd frontend && set -a && source ../.env && set +a && npm run dev`
  (Next on `:3000`). The `next.config.js` rewrite proxies `/api/*` to
  `BACKEND_URL`, so there's no CORS / nginx in this setup. The `set -a / source`
  dance is needed because Next only auto-loads `.env` from the frontend dir,
  not the repo root.
- **Env loading** — `app/config.py` calls `dotenv.load_dotenv()` against the
  repo-root `.env`, so the backend and worker can be launched directly
  (`python -m app.main`, pytest, task worker) without sourcing the env first.
- **Open at** http://localhost:3000 (not `:8080` — no nginx).
- **Readiness** — `curl -sf http://localhost:8080/api/health` and
  `curl -sf http://localhost:3000`.
- **Frontend cache wedge** — if pages stick on "Loading…" with 404s on
  `/_next/static/chunks/*`, the dev cache is corrupted: stop the dev server,
  `rm -rf frontend/.next`, restart.

### Cross-area design rules
The interfaces and seams that hold across areas. CLAUDE.md is the canonical
rulebook; per-area docs reference these and add their own area-specific rules.

- **Single LLM seam.** `app/llm/client.py:complete()` is the only path to a
  provider. See [agents/chat-agent.md](agents/chat-agent.md) and
  [agents/document-updater.md](agents/document-updater.md).
- **SQLAlchemy 2.0 ORM, small repo modules.** Repos open a `session()`
  from `app/db/session.py`, return plain dicts, and never leak ORM rows
  to callers. See [flask-and-apis](flask-and-apis/flask-and-apis.md).
- **Wiki is git-backed.** `app/wiki/git.py` is the only entry point that
  shells out to git.
- **Triggers are git-backed YAML with a Postgres cache.** The
  `<dir>/.trigger_*.yaml` file is canonical; the `triggers` row in
  Postgres is a denormalized cache for fan-out. CRUD goes through
  `app/triggers/storage.py` (file) + `app/triggers/repo.py` (cache).
- **Chat agent loop is pure** (messages-in / messages-out). The HTTP layer
  owns persistence. See [agents/chat-agent.md](agents/chat-agent.md).
- **Tasks via pgmq.** Anything taking >100ms goes to the worker. See
  [background-tasks](background-tasks/background-tasks.md).
- **Frontend network/auth via `lib/api.ts` + `lib/auth.tsx`.** No raw
  `fetch`, no raw session reads. See [frontend](frontend/frontend.md).

### Data model (applied schema)

| Table | Purpose | Owned by |
|---|---|---|
| `users`           | id, email, name, password_hash, is_admin, created_at | flask-and-apis |
| `mcp_connections` | per-user MCP server entries | flask-and-apis |
| `documents`       | metadata only — body lives in git | flask-and-apis |
| `triggers`        | Postgres cache of inline `<dir>/.trigger_*.yaml` files | natural-language-triggers |
| `events`          | append-only audit log | flask-and-apis (write surface), frontend (events view) |
| `documents_fts`   | BM25 virtual table (porter+unicode61, bm25) | flask-and-apis (search), background-tasks (reindex) |
| `groups`, `group_members` | user groups for permission grants | permissions |
| `wiki_owners`     | per-page owner (path → user_id) | permissions |
| `acl_entries`     | grants of read/write to user/group/everyone on a page or folder | permissions |
| `llm_settings`    | single-row provider/model/keys | flask-and-apis (admin) |
| *(removed — schema is driven by `app/db/models.py`)*

### Key cross-area design decisions

| Date | Decision | Why |
|------|----------|-----|
| 2026-05-06 | LLM keys move to DB, not env | Admin UI ergonomics; env was tedious for non-eng dogfooders |
| 2026-05-06 | First user auto-admin | Removes pre-seeded admin password footgun |
| 2026-05-06 | Default entry = file tree (Wiki tab) | Mental model is "open the wiki" |
| 2026-05-06 | Triggers v0 = record-event only, no webhook dispatch | Ship the loop; integrations come after we trust the eval |
| 2026-05-06 | Sidebar = Wiki / Triggers / Events; chat is a side panel | Chat needs to follow the user across pages and stay context-aware |
| 2026-05-06 | Triggers per-user (owner-only visibility) in v0 | Simplest model that ships; sharing is backlog |
| 2026-05-06 | Chat propose-and-apply for writes | No silent edits while we lack eval data |
| 2026-05-06 | Postgres + SQLAlchemy 2.0 ORM with small dict-returning repos | Real DB semantics (LISTEN/NOTIFY, advisory locks, multi-replica) without leaking ORM rows past the repo |
| 2026-05-06 | Single LLM seam (`app/llm/client.py:complete`) | Provider-swappable; one place to mock in tests |
| 2026-05-06 | Mock LLM SDKs at the per-provider `_client`, not at `complete` | Lets tests exercise the real translation layer |
| 2026-05-06 | Chat agent loop is pure (messages-in / messages-out) | Persistence wired separately at the HTTP edge |
| 2026-05-07 | LLM providers are a plural seam at `app/llm/providers/` (Anthropic, OpenAI Responses, Gemini, Ollama) | Adding a backend = drop a module + register, no `client.py` if/elif growth |

### Open cross-area questions
- **LLM cost** — every doc commit fans out to a doc-updater pass + N trigger
  evaluations. Need batching / debounce. Acceptable for v0 dogfooding scale?
- **Doc bloat / loss** — the doc-updater agent must avoid both. No eval
  data yet. (See [agents/document-updater.md](agents/document-updater.md).)
- **Agent hand-off discipline** — exploration work; tracked in
  [exploration](exploration/exploration.md).
- **Concurrency on the wiki repo** — git operations are serialized in one
  worker today. Multi-worker needs a per-repo lock. (See
  [background-tasks](background-tasks/background-tasks.md).)
- **Onyx → agent-wiki push contract** — see
  [onyx-push](onyx-push/onyx-push.md).
- **Triggers schema vs. UX** — `triggers.action_json` exists in schema but
  is unused in v0. (See [natural-language-triggers](natural-language-triggers/natural-language-triggers.md).)

For file-by-file detail, area-local design, status, and the work breakdown,
follow the per-area links above.

---

## 4. Status snapshot

One line per area; the per-area doc has the real picture.

| Area | Status | Detail |
|---|---|---|
| Flask + APIs | Auth + admin live; documents read-only; triggers/events/webhooks/MCP/users stubs | [flask-and-apis](flask-and-apis/flask-and-apis.md) |
| Document-updater agent | Stub; system+user prompts written | [agents/document-updater.md](agents/document-updater.md) |
| Chat agent | Loop primitive + stateless HTTP wired (SSE streaming); tools off; persistence stub. Next: location context, propose-and-apply tools, wiki traversal | [agents/chat-agent.md](agents/chat-agent.md) |
| NL triggers | CRUD API + repo + Triggers tab + create modal live (Postgres cache + git YAML); fire-path on human edits live; time-based stubs | [natural-language-triggers](natural-language-triggers/natural-language-triggers.md) |
| Frontend | Auth/admin/wiki-read/chat live; chat needs to move from `/chat` page to a side panel; sidebar needs to become Wiki/Triggers/Events; no editor, no inline-triggers panel, no events view yet | [frontend](frontend/frontend.md) |
| Onyx push | Not started; ingest endpoint stub | [onyx-push](onyx-push/onyx-push.md) |
| Background tasks | Reindex live; doc-update + periodic stubs; trigger fan-out task TBD | [background-tasks](background-tasks/background-tasks.md) |
| MCP server (inbound) | **Full surface shipped (Phases 1–7).** Phase 1: tokens + Agents sidebar page (mint / reveal-once / revoke). Phase 2: bearer-authed JSON-RPC dispatcher at `POST /api/mcp` (`g.user` seam so `require_can`/ACL Just Work). Phase 3: read tool surface (`read_doc`, `search_wiki`, `list_history`, `ask_nl_question`) with allow-list. Phase 4: write tool surface (`edit_doc`, `multi_edit`, `write_doc`, `apply_patch`, `move_path`, `create_directory`) — `base_sha` optimistic concurrency, `base_sha_required_for_overwrite` for `write_doc`, `stale_paths` field on every result. Phase 5: `resources/{list,read,subscribe,unsubscribe}` over `wiki:///<path>`; long-lived SSE side channel on `GET /api/mcp`; in-memory subscription registry + per-session queue + Postgres `LISTEN/NOTIFY` bridge for cross-process commits; `read_doc` auto-subscribes at HEAD; per-subscriber ACL recheck before delivery. Phase 6: async `update_doc_nl` (`mcp_jobs` table, `agent_update_document_nl(job_id)` worker on `documents_queue`); idempotency key dedupes retries; 30s same-(user,path) debounce; worker reconstitutes `g.user` via `worker_context.as_user`; `job://<id>` resource read + subscribe with cross-user isolation; `publish_job_update` pushes status changes over SSE. Phase 7: operator-facing setup guide at `docs/mcp-server.md` (Claude Code / Cursor / Codex / Python-SDK examples + troubleshooting); `update_doc_nl` and `edit_doc` tool descriptions tuned with explicit batching guidance. Open questions in the design doc track follow-on work (rate limits, per-token scoping, tree templates) | [mcp-server](mcp-server/mcp-server.md) |
| Exploration | Not started; parking lot for MCP-vs-skill question | [exploration](exploration/exploration.md) |
| Infra | Compose + volumes wired; EKS Terraform + Helm chart in `deploy/` (validated, not yet end-to-end applied) | [infra](infra/infra.md) |

---

## 5. Decision log (chronological, terse)

Cross-cutting decisions only — area-specific design choices live in the
area docs.

- **2026-05-06** — Repo scaffolded; backend (Flask + Postgres + pgmq) and
  frontend (Next.js) skeletons in place; basic auth + signup with optional
  email whitelist; admin UI for user/LLM management; wiki page renders
  markdown from a list+file endpoint.
- **2026-05-06** — Product/UX direction locked: 3-tab sidebar (Wiki / Chat /
  Events), Wiki is default, file-tree → directory page → reader → editor
  (Save/Cancel), triggers on doc and directory pages. v0 trigger action is
  record-event-only.
- **2026-05-06** — `agents.md` feature **deferred**; do not implement.
- **2026-05-06** — Trigger extensions (outbound dispatch, ambient surfacing)
  **deferred**; do not implement past "evaluate + record event".
- **2026-05-06** — Persona priority is informational only; build for both
  eng and GTM use.
- **2026-05-06** — Chat loop primitive landed (`run_chat_loop`) — pure
  message-list-in / message-list-out, tools off by default.
- **2026-05-06** — Backend pytest harness landed; convention: mock SDKs at
  the per-provider `_client` seam; never import the real provider SDKs
  from tests; use a real per-test Postgres schema via the `tmp_db` fixture.
- **2026-05-06** — V0 brief preserved verbatim under §2 as the durable
  reference.
- **2026-05-06** — Admin UI split from a single tabbed page into three
  routes (`/admin`, `/admin/users`, `/admin/llm`).
- **2026-05-06** — Manual FTS reindex path wired:
  `POST /api/documents/reindex` enqueues `tasks.reindex.reindex_path`;
  wiki file-viewer has a "Reindex" button. Auto-reindex on commit deferred.
- **2026-05-06** — Split per-area design/progress docs into sibling
  directories under `local_data/wiki/`. The master doc is the cross-area
  map; per-area docs own deeper detail.
- **2026-05-06** — Master doc compressed: file-by-file detail, area-local
  status, and the A–M work breakdown moved to per-area docs. Master keeps
  product spec, V0 brief, cross-cutting architecture, status snapshot,
  decision log.
- **2026-05-06** — Trigger fire-path landed: human edits via `PUT /api/documents/file`
  now enqueue `tasks.triggers.fan_out_trigger_eval` after `commit_file`,
  which runs the SQL match + NL evaluator and writes a `trigger.fire`
  events row on match. v0 stays record-only — no outbound dispatch.
  Trigger CRUD/storage/UI still stubs; rows must be seeded via SQL to
  exercise the path.
- **2026-05-06** — Trigger CRUD + UI landed. Backend: `app/triggers/repo.py`
  (Postgres cache) and full `/api/triggers` CRUD (owner-scoped, kind=delta
  only). Frontend: new "Triggers" sidebar item between Chat and Events
  (`/triggers`); reusable `<TriggerModal>` opens both from the Triggers tab
  and a new "+ Trigger" button on the wiki doc reader (with `scope_path`
  pinned to the current doc).
- **2026-05-07** — Trigger storage moved to git-backed inline YAML
  (`<dir>/.trigger_<id>*.yaml`), with the Postgres `triggers` row as a
  denormalized cache. `app/triggers/storage.py` owns the file write;
  `app/triggers/repo.py` owns the cache. `rebuild_from_filesystem`
  re-converges the cache on boot.

- **2026-05-06** — Documented host-run dev workflow (no Docker) under §3
  "Local dev — running on the host". Compose stays the canonical path;
  host run is for fast iteration. `.env` already points data paths at
  `local_data/` and sets `BACKEND_URL` so the Next rewrite proxies `/api/*`
  to Flask without nginx.

- **2026-05-06** — LLM client refactored to streaming. `stream()` is the new
  primitive (yields `text_delta` / `tool_call` / `done` events); `complete()`
  is a thin drainer kept for trigger evaluator + doc-updater. **OpenAI moved
  to the Responses API** (`responses.create(stream=True)`, with `instructions`
  for system, flat function-tool envelope, `function_call` /
  `function_call_output` items, `max_output_tokens`). Anthropic uses
  `messages.stream`. Chat agent gained `run_chat_loop_stream` that yields
  events while preserving in-place message mutation. `POST /api/chat/messages`
  is now SSE (`text/event-stream`, `data: {...}\n\n` frames, terminal `done`
  or `error` event). Frontend chat reads the stream via a new
  `apiStream` helper in `src/lib/api.ts` and renders text deltas live.
  Required `openai>=1.50` for the Responses API.

- **2026-05-06** — **Chat is a collapsible side panel, not a top-level tab.**
  Always available; carries the user's current wiki location with each
  message. Two new tool primitives — `propose_doc_edit` and
  `propose_create_trigger` — implement the **propose-and-apply contract**:
  the agent emits a draft into the chat thread, the user clicks Apply to
  commit. No silent writes from the chat agent. Sidebar tabs become
  Wiki / Triggers / Events.
- **2026-05-06** — **Triggers are per-user in v0.** Every trigger has an
  owner; only the owner sees, edits, and receives events from their
  triggers. The trigger fires when the scoped path changes regardless of
  who edited; visibility and the resulting event are gated on ownership.
  Sharing/collaboration is backlog (tracked under
  [natural-language-triggers](natural-language-triggers/natural-language-triggers.md)).
- **2026-05-06** — Triggers are visible in two places: a top-level
  Triggers tab (the user's full list) and inline panels on doc/directory
  pages (the user's triggers scoped to that path).
- **2026-05-06** — **Chat agent must be able to traverse the wiki and
  update associated pages**, not only answer about the current page. v0
  tool minimum: `search_wiki` (bm25) + `read_doc` + `list_dir` + the
  propose-and-apply write family. **Open question:** add a sandboxed
  read-only `wiki_shell` tool (ls/grep/cat/find against the wiki working
  tree) for richer multi-step exploration — decision deferred; revisit
  if the structured tools feel clunky in dogfooding.

- **2026-05-07** — **LLM seam refactored into a plural seam at
  `app/llm/providers/`.** `client.py` keeps `stream` / `complete` as the
  public surface; one module per backend (`anthropic`, `openai`,
  `gemini`, `ollama`) implements the `Provider` protocol
  (`name`, `check_configured(settings)`,
  `stream(messages, *, model, tools, max_tokens, settings)`). Adding a
  backend = drop a module + register; no if/elif edits in `client.py`.
  `LLMError` moved to `app/llm/errors.py` (no client.py back-compat
  re-export). The admin UI now exposes provider/model + per-provider
  credentials (Anthropic key, OpenAI key, Gemini key, Ollama base URL).
  Required `google-genai>=1.0` and `ollama>=0.4`; the `LLMSettings` model added
  `gemini_api_key` and `ollama_base_url` columns.
- **2026-05-07** — **EKS + Helm deploy scaffolding landed under `deploy/`.**
  Terraform (`deploy/terraform/`) provisions VPC + EKS via the community
  `terraform-aws-modules/{vpc,eks}/aws` modules, wires the EBS CSI add-on
  with IRSA, sets a `gp3` default StorageClass, and helm-installs
  ingress-nginx (NLB) + cert-manager (with an optional `letsencrypt-prod`
  ClusterIssuer). State is local + gitignored. Helm chart
  (`deploy/helm/agent-workspace/`) deploys backend + worker + frontend with
  one PVC (`wiki-data` 10Gi, RWO `gp3`); backend and worker are pinned to
  one replica with `Recreate` strategy and `podAffinity` co-scheduling
  because the wiki working tree has no concurrent-write story (and the
  in-process periodic-task scheduler must be single-replica). The in-cluster nginx pod from
  compose is **dropped** — the Ingress handles `/api/*` → backend, `/` →
  frontend directly. Image registry assumed to be a public GHCR/Docker Hub
  repo (no ECR provisioning). Two-step flow: `terraform apply` once per
  env, then `helm upgrade --install` per app deploy. See `deploy/README.md`.

- **2026-05-08** — **LLM observability consolidated to the seam.** The
  full request + full response are now DEBUG-dumped exactly once per
  call, inside `app/llm/client.py:stream()`. Provider modules
  (`anthropic`, `openai`, `gemini`, `ollama`) no longer pretty-print
  their request kwargs — that was duplicating the seam's "llm request
  messages" / "llm request tools" output. `stream()` also accumulates
  the response (text + tool_calls + stop_reason + usage) and emits a
  single "llm response" entry on done, so streaming callers (chat) get
  the same dump that `complete()` callers (triggers, doc-updater) used
  to. The unused `debug_dump` helper in `app/llm/providers/_common.py`
  was removed.

  Normalized `usage` dict gained an optional `reasoning_tokens` field
  alongside `input_tokens` / `output_tokens`. OpenAI surfaces it from
  `usage.output_tokens_details.reasoning_tokens`; Gemini from
  `usage_metadata.thoughts_token_count`; Anthropic and Ollama emit `0`.

- **2026-05-08** — **Background work split into three queues.** One
  `TaskQueue` instance per queue, each backed by its own pgmq queue in
  the app's Postgres (`pgmq.q_documents`, `pgmq.q_triggers`,
  `pgmq.q_wiki_bm25`): `documents_queue` (LLM doc-reconciliation —
  `update_document_*`), `triggers_queue` (NL trigger
  eval, both `fan_out_trigger_eval` and the cron
  `evaluate_scheduled_triggers`), `wiki_bm25_queue` (BM25
  reindex). Three worker containers in `docker-compose.yml`
  (`worker-documents`, `worker-triggers`, `worker-wiki-bm25`),
  each launched via `python -m app.tasks.run_worker <queue>`. Goal:
  isolate slow LLM work from the cheap indexer and from trigger fires
  so each queue's backlog only delays its own consumers. See
  [background-tasks](background-tasks/background-tasks.md).

- **2026-05-08** — **Root-scoped trigger fan-out fix.** `find_matching_triggers`
  was building its `IN (...)` candidate set from `[doc_path,
  *parent_dirs(doc_path)]`, but `parent_dirs` never returned `""` — so a
  trigger with `scope_path = ''` (the wiki root convention from
  `app/triggers/storage.py:compute_path`) never matched any doc.
  `app/wiki/filesystem.py:parent_dirs` now appends `""` to its output;
  regression test in `tests/test_triggers_engine.py`.

- **2026-05-08** — **Helm chart published as a Helm repo from `gh-pages`**
  via `.github/workflows/helm-release.yml` (chart-releaser-action). Chart
  shape evolved from the initial scaffold: one `Deployment` per pgmq
  queue (`documents`, `triggers`, `wiki_bm25`); single-AZ node group;
  AWS LB Controller for ingress (`type=external`, `nlb-target-type=ip`,
  `scheme=internet-facing`) instead of the legacy in-tree NLB. The
  in-tree NLB doesn't open NodePort to `0.0.0.0/0` and defaults to
  `internal` scheme, both of which leave the LB unreachable from the
  public internet. See [infra/infra.md](infra/infra.md) for the live
  shape.

- **2026-05-08** — **OIDC auth wired end-to-end (Google).** The
  `app/auth/oidc.py` stub was replaced with a real authorization-code
  flow via authlib's Flask integration. New routes:
  `GET /api/auth/oidc/login` (redirect to IdP) and
  `GET /api/auth/oidc/callback` (exchange code → validate
  `email_verified` + `ALLOWED_EMAILS` → upsert user → start session).
  First OIDC user is auto-admin, same convention as basic. Frontend
  swaps the email/password form for a "Sign in with Google" button when
  `auth_config.mode == "oidc"`; `/signup` redirects to `/login` in OIDC
  mode. Chart's `_helpers.tpl` derives `OIDC_REDIRECT_URI` from
  `ingress.host` automatically. `SECURE_COOKIES` env now wires
  `SESSION_COOKIE_SECURE` (was hard-coded `False`). 14 unit tests in
  `tests/test_auth_oidc.py` cover upsert + every callback branch.

- **2026-05-08** — **Automated dogfood deploy live.** Image build and
  cluster rollout are now cron-driven across two repos. Build side:
  `agent-wiki:.github/workflows/nightly-build.yml` (10 UTC daily +
  `workflow_dispatch`) matrix-builds backend + frontend multi-arch and
  pushes `onyxdotapp/agent-wiki-{backend,frontend}:nightly-latest-YYYYMMDD`
  to Docker Hub. Deploy side: a corresponding workflow in the private
  cluster repo runs an hour later (11 UTC + dispatch with `version_tag`
  input), probes Docker Hub for both images at the requested tag,
  assumes an IAM role via GitHub OIDC, pulls `SECRET_KEY` + the OIDC
  client secret from AWS Secrets Manager, and runs `helm upgrade
  --install` with `--set image.{backend,frontend}.tag` + the secrets.
  Slack notifications fire on kickoff/result for ad-hoc runs and on
  failure for scheduled. `ods deploy wiki` (shipped in the
  `onyx-devtools` PyPI package) wraps the chain end-to-end so an ad-hoc
  deploy is a single command. The pre-existing `v*` tag-driven build
  (`docker-build-push.yml`) is retained for cutting named releases; the
  automated path uses the date-rolled `nightly-latest-*` tag instead so
  the deploy side doesn't have to chase a moving "latest". Secrets in
  AWS Secrets Manager replaces the prior `helm --set` from 1Password.
  See [infra/infra.md](infra/infra.md) for the full shape.

_(Append new entries with a date prefix. Cross-cutting only.)_
