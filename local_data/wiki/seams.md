# Seams

The single auditable list of every interface boundary in the codebase.
Every PR that adds a new "only-place-X-can-happen" rule should add a row
here. Every PR that bypasses one should be rejected.

A **seam** is a place where the rest of the system is *required* to go
through one entry point. Two reasons we declare seams:

- **Single-impl seams** — only one implementation is ever expected (git
  wrapper, LLM client, session reader). The seam exists so we can swap or
  test the dependency, and so risky I/O lives in exactly one file.
- **Plural seams** — multiple implementations coexist behind a registry
  (trigger actions, ingestion sources). The seam exists so adding a new
  variant is a registry insertion, not an `if/elif` edit.

## Conventions

- **Single-impl seams** live as a module at a stable path (e.g.
  `app/wiki/git.py`). The rule is "don't import the underlying SDK / call
  the underlying syscall outside this module."
- **Plural seams** live as a directory: `app/<area>/<plural>/` with
  `__init__.py` exposing a `Protocol` + `register()` + `dispatch()`, and
  one file per implementation. Use `typing.Protocol`, not ABCs.
- Tests **patch at the seam**, never at the SDK below it.
- New seam → add a row here in the same PR that introduces it.

## Existing seams

### Backend — single-impl

| Seam | File | Rule |
| --- | --- | --- |
| LLM facade | `backend/app/llm/client.py` (`stream`, `complete`) | Only entry point for LLM calls; dispatches to the configured provider. Tests patch `app.llm.client.stream` / `complete` (or, for SDK-shape verification, the per-provider `_client`). |
| LLM provider config | `backend/app/llm/settings.py` (`get()`) | Don't read `CONFIG.anthropic_api_key` or `os.environ` provider keys elsewhere. |
| LLM error type | `backend/app/llm/errors.py` (`LLMError`) | Sole user-presentable LLM error. Always import from `app.llm.errors` (not `app.llm.client`). |
| Auth gating | `backend/app/auth/__init__.py` (`@login_required`, `@admin_required`, `current_user()`) | Routes use the decorators. Don't read `flask.session["user_id"]` outside `app/auth/`. |
| Git operations | `backend/app/wiki/git.py` | No `subprocess.run(["git", ...])` anywhere else. |
| Wiki path safety | `backend/app/wiki/filesystem.py` (`safe_rel_path`) | All user/agent-supplied wiki paths flow through this. |
| Wiki edit primitive | `backend/app/wiki/edit.py` (`replace`) | Pure fuzzy find-and-replace (9-strategy chain). All doc-edit tools call this — no other places do find-and-replace on wiki bodies. |
| Wiki link checker | `backend/app/wiki/links.py` (`find_broken_links`) | Markdown LSP analogue. Tools call after a write to surface broken links to the model. |
| Post-write notify | `backend/app/wiki/notify.py` (`after_doc_write`, `after_doc_delete`, `after_path_move`) | The single seam every successful wiki `.md` mutation goes through. Runs FTS reindex + `fan_out_trigger_eval`. **Both API handlers (`api/documents.py`) and chat-agent tools (`tools/_doc_helpers.py`, `tools/move_path.py`) call it.** Trigger YAMLs (`storage.py`) deliberately bypass — they're config, not docs. |
| Bash execution | `backend/app/llm/agents/tools/_bash.py` (`run`, `execute_chain`, `parse_chain`) | The only place `subprocess.run` runs LLM-emitted shell. Allowlist gate + per-segment re-validation + cwd pinned to `CONFIG.wiki_dir` + per-command timeout + truncation. Don't shell out to model-supplied strings anywhere else. |
| Web search | `backend/app/web/__init__.py` (`search`, `search_provider`) | Serper-only today; callers don't import `app.web.serper` directly. Tests patch `app.web.search_provider`. |
| Web crawl | `backend/app/web/__init__.py` (`fetch`, `crawl_provider`) | Firecrawl-only today; same rule. |
| Web provider config | `backend/app/web/settings.py` (`get()`) | Don't read provider keys from `os.environ` or `CONFIG`; admin UI is the only way. |
| Chat-loop session state | `backend/app/llm/agents/_session.py` (`seen_doc_paths` ContextVar) | The chat loop populates from `read_page` results (NOT `search_wiki` — its snippets are too short to count as a read); doc-edit tools read it for read-before-write. Tools must tolerate the default `None` so they work outside a loop. |
| DB connection | `backend/app/db/sqlite.py` (`connect()`, `init_db()`) | All repos open via `connect()`; no other sqlite entry points. |
| Migrations | `backend/app/db/migrations/*.sql` | Lex-sorted, applied once. Never edit an applied file; add a new one. |
| Background work | `backend/app/tasks/` (Huey decorators) | Anything > ~100ms or that hits the LLM enqueues a task; no ad-hoc threads. |
| HTTP error envelope | `{"error": "<msg>"}` (see `backend/app/api/auth.py`) | All API errors use this shape; the frontend's `ApiError` parses it. |

### Backend — plural (registry)

| Seam | Directory | Protocol | Status |
| --- | --- | --- | --- |
| Agent tools | `backend/app/llm/agents/tools/` | `<name>.json` spec + `<name>.py` exposing `handle(args) -> Any` | `__init__.py` registers all pairs at import time; `dispatch(name, args)` is the call seam. Adding a tool = drop a new pair. |
| LLM providers | `backend/app/llm/providers/` (today: `anthropic.py`, `openai.py`, `gemini.py`, `ollama.py`) | `Provider` (`name`, `check_configured(settings)`, `stream(messages, *, model, tools, max_tokens, settings) -> Iterator[StreamEvent]`) | Each module exposes a `PROVIDER` instance and registers itself at import. `client.py` dispatches by `settings.provider`. No `import anthropic`/`openai`/`google.genai`/`ollama` outside the matching provider module. |
| Trigger evaluators | `backend/app/triggers/` (today: `natural_language.py`, `time_based.py`) | `(kind, find_candidates, evaluate)` | Existing but not yet behind a Protocol; formalize when a 3rd kind is proposed. |

### Frontend

| Seam | File | Rule |
| --- | --- | --- |
| Network | `frontend/src/lib/api.ts` (`apiFetch`) | No raw `fetch` in pages or components. |
| Auth | `frontend/src/lib/auth.tsx` (`useAuth`, `useRequireAuth`, `AuthProvider`) | No component calls `/api/auth/me` directly. |
| Top-level chrome | `frontend/src/components/common/AppShell.tsx` | Top-level pages wrap their content in `<AppShell>`. |
| Markdown rendering | `react-markdown` + `remark-gfm` | Don't inject HTML from the backend. |

## Planned seams

These are the surfaces where a second implementation is imminent. Pin the
shape *before* the second implementation lands so it doesn't fork.

### Plural — design as a registry from day one

| Seam | Planned location | Shape | Why now |
| --- | --- | --- | --- |
| Trigger action dispatcher | `backend/app/triggers/actions/` | `ActionHandler = Protocol(kind: str, handle(trigger, context) -> None)` | `engine.dispatch` is a `NotImplementedError` listing `webhook \| http \| agent_message`. The first one will set the shape; design the registry first. |
| Ingestion sources | `backend/app/ingestion/` | `IngestionSource = Protocol(parse(req) -> DocChange, enqueue(change) -> None)` | `api/webhooks.py` is a 13-line stub; Onyx push is next. Two sources without a shared shape = duplicate auth/idempotency/rate-limiting. |
| Identity providers | `backend/app/auth/providers/` | `IdentityProvider = Protocol(authenticate(req) -> NormalizedIdentity)` | `auth/basic.py` + `auth/oidc.py` already split; formalize before SAML or a 2nd OIDC. |

### Single-impl — formalize the entry point

| Seam | Planned location | Why |
| --- | --- | --- |
| Agent runtime / loop | `backend/app/llm/agents/loop.py` | `chat.py` (176 lines, streaming, multi-turn tool loop) and `document_updater.py` (27 lines, single-shot) will diverge. Extract `run_chat_loop_stream` to a shared loop both call. |
| Prompt loader | `backend/app/llm/prompts/__init__.py:load_prompt` | Already exists implicitly. Make it the only way prompts are read so admin-editable prompts (likely soon) plug in here. |
| Search backend | `backend/app/wiki/search.py:search` | Pin the return shape (`{path, title, score}`) in a model so the chat tool and any future surface don't bind to `sqlite3.Row`. FTS today, vector later. |
| Event sink | `backend/app/events/repo.py:record(kind, actor, payload)` | Trigger fires, doc updates, agent runs all want to write events. One repo function so audit/metrics later isn't a refactor. |
| MCP server (inbound) tool surface | `backend/app/mcp_server/__init__.py` | Mounts `/api/mcp` (Streamable HTTP). Tool registry merges the agent-tool registry with mcp-only tools (`update_doc_nl`, `apply_patch`, `ask_nl_question`, `read_doc(sha)`, `list_history`). See [mcp-server](mcp-server/mcp-server.md). |
| MCP client (outbound) connections | `backend/app/api/mcp_connections.py` | User-managed list of external MCP servers our agent harness consumes. Currently lives in `app/api/mcp.py`; rename when the inbound surface lands. |
| Wiki commit pub-sub | `commit_and_fan_out` in `backend/app/llm/agents/tools/_doc_helpers.py` | Single seam every wiki write goes through. Already fans out to reindex + trigger eval; the MCP server adds a third subscriber for `notifications/resources/updated`. |
| Frontend streaming | `frontend/src/lib/stream.ts` | Chat streams now; trigger-evaluation progress likely will. Keep `apiFetch` for request/response, put SSE behind its own typed function. |

## How to add a new seam

1. Decide single-impl vs plural.
2. Single-impl: pick a stable path, write a one-line rule ("don't import X outside Y"). Plural: create the `app/<area>/<plural>/` dir, declare the `Protocol` and registry in `__init__.py`.
3. Add a row to this page **in the same PR**.
4. If single-impl, add the import rule to `CLAUDE.md` under "What not to do."
5. Optional: add an `importlinter` contract to enforce the rule in CI.

## How to audit

- Grep for the seam's identifier across the codebase to confirm no
  bypasses crept in (e.g. `rg "subprocess.*git" backend/app | rg -v
  "wiki/git.py"`).
- Walk this page top-to-bottom during architecture review.
- When a new contributor asks "where do I add X?", point them here first.
