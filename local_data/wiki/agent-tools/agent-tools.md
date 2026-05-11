# Agent tools

Reference for every tool in the shared agent-tool registry. The registry
is the single seam every tool-using surface goes through:

- **In-process chat agent** (`app/llm/agents/chat.py`) — wires
  `tool_registry.TOOL_SPECS` + `tool_registry.dispatch` into the chat
  loop primitive.
- **MCP server (planned)** — same registry, plus a few mcp-only tools.
  See [mcp-server](../mcp-server/mcp-server.md).
- **Sub-agents** that need a curated subset (e.g. the wiki Q&A agent
  passes only `search_wiki` + `read_page` to its loop).

Source of truth for each tool: the `<name>.json` spec + `<name>.py`
handler in `backend/app/llm/agents/tools/`. JSON is what the LLM sees;
the Python handler is what runs server-side.

## Registry pattern

```
backend/app/llm/agents/tools/
  __init__.py            # auto-loads every <name>.json + <name>.py at import
  _doc_helpers.py        # shared: validate path, optimistic-concurrency, commit + fan-out
  _bash.py               # shared bash execution primitive
  <name>.json            # function-call spec — the LLM-facing contract
  <name>.py              # `handle(args: dict) -> Any` — server-side dispatch
```

Adding a tool = drop a new `<name>.{json,py}` pair. The JSON's `name`
field MUST equal the filename stem AND the Python module name; the
loader fails loud at startup if any of those drift.

## Synchronous, all of them

Every tool is **synchronous from the caller's perspective**:
`dispatch(name, args)` blocks until the handler returns a result dict.
There is no async/await tool surface today. A few tools (`ask_nl_question`,
`update_doc_nl`) call into the LLM internally and can take 10–60s per
call; the chat loop happily waits, and the MCP layer will likely wrap
the slow ones in async jobs (returning a `job_id` + push notifications)
when it lands — but the tool *handler* itself stays sync. See
[mcp-server.md → NL update tool](../mcp-server/mcp-server.md) for the
async wrapper plan.

Errors are returned as `{"error": "<message>"}` instead of raised. The
chat loop stringifies the result dict and feeds it back to the model on
the next turn so it can self-correct.

## Tool inventory

Legend:

- **Latency:** `fast` (<100ms, no I/O beyond Postgres/git), `slow` (LLM call
  or web fetch — seconds), `varies` (depends on argument size).
- **Writes:** `none` (read-only), `git+fts+triggers` (commit via
  `commit_and_fan_out` → FTS reindex + NL-trigger fan-out via
  `app.wiki.notify.after_doc_write`), `db` (Postgres-only side effect).

### Discovery / read

| Tool | Inputs | Returns | Latency | Writes | Notes |
| --- | --- | --- | --- | --- | --- |
| `search_wiki` | `query: str`, `limit?: int` (≤20) | `{results: [{path, title, snippet, score}]}` | fast | none | BM25 over the BM25 index. Snippets are ~64 tokens with matches in `**bold**`. Snippets are not enough to safely edit a doc — call `read_page` / `read_doc` first to grab the full body and a `base_sha`. |
| `read_page` | `path: str` (`.md`) | `{path, title, body}` | fast | none | Full HEAD body. Returns the doc's current sha (via `agents` payload) so you can pass it to a subsequent edit as `base_sha`. |
| `read_doc` | `path: str`, `sha?: str` | `{path, body, sha, is_head}` | fast | none | Like `read_page` but accepts an optional commit SHA for historical reads. Pass the returned HEAD `sha` to a write tool as `base_sha` for optimistic-concurrency. |
| `list_history` | `path: str`, `limit?: int` (≤100, default 20) | `{path, history: [{sha, author, ts, message}]}` | fast | none | Newest-first, follows renames (`git log --follow`). Use to find a sha to pass to `read_doc`. |
| `ask_nl_question` | `query: str` | `{answer: str, sources: [{path}]}` | slow (LLM) | none | Spawns a one-shot read-only sub-agent (`app.llm.agents.wiki_qa`) with `search_wiki` + `read_page` and a 6-iteration cap. Synthesized answer + the doc paths it actually fetched. |
| `run_bash` | `command: str` | `{output, exit_code, elapsed_ms, truncated}` | varies | none | **Backup tool.** Read-only Unix commands (`cat, find, grep, ls, head, tail, wc`) chained with `|` / `&&` / `||` / `;`. cwd pinned to wiki root, 30s/segment timeout, output capped at 2000 lines / 50 KB (100 lines if the chain ends in `grep` / `find`). Allowlist enforced upfront before any segment runs — no smuggling via `xargs`. |
| `explain_functionality` | _(none)_ | canonical reference text | fast | none | Static docstring describing what agent-wiki is. Use only when the user asks meta-questions about the app itself. |

### Doc edits / writes

All commit through `commit_and_fan_out`, which fires
`app.wiki.notify.after_doc_write` (FTS reindex + NL-trigger fan-out;
the planned MCP pubsub will hook in here too — see
[seams.md](../seams.md)).

Concurrency is handled by **optional `base_sha`**: pass the sha you last
read for the doc; the write returns `{error: "stale_base", base_sha,
current_sha}` if HEAD has drifted, so you can re-read and re-derive the
edit. `write_doc` makes `base_sha` **required** for overwrites of
existing files (full-body has no fuzzy fallback if HEAD moved). The
other write tools accept it optionally.

| Tool | Inputs | Returns | Latency | Writes | Notes |
| --- | --- | --- | --- | --- | --- |
| `edit_doc` | `path`, `old_string`, `new_string`, `replace_all?`, `commit_message`, `base_sha?` | `{path, sha, diff, broken_links}` _or_ `{error: "stale_base", ...}` | fast | git+fts+triggers | Surgical find-and-replace. Fuzzy match via the 9-strategy chain in `app.wiki.edit:replace`. Errors `old_string not found` or `multiple matches` — caller adds context and retries. |
| `multi_edit` | `path`, `edits: [{old_string, new_string, replace_all?}]`, `commit_message`, `base_sha?` | `{path, sha, diff, broken_links, applied_count}` _or_ `{error: "stale_base", ...}` | fast | git+fts+triggers | Atomic batch: each edit applies against the result of the previous; any failure aborts the whole batch with no commit. One commit, one reindex, one fan-out. |
| `write_doc` | `path`, `body`, `commit_message`, `base_sha?` | `{path, sha, diff, broken_links, created}` _or_ `{error: "base_sha_required_for_overwrite" \| "stale_base", ...}` | fast | git+fts+triggers | Full-body create or overwrite. Use **only** for new docs or wholesale rewrites (>50% of lines changing). Overwrites of existing files **require** `base_sha`. |
| `apply_patch` | `path`, `patch`, `commit_message`, `base_sha?` | `{path, sha, diff, broken_links}` _or_ `{error: "stale_base", ...}` | fast | git+fts+triggers | Unified-diff editor. Each hunk tries line-anchored apply first (the `@@ -L,N` header is honored); on drift falls back to `wiki.edit.replace`. Atomic across hunks. Pure-insertion hunks (no context) require a valid line anchor — no fallback. |
| `update_doc_nl` | `path`, `instruction`, `base_sha?` | `{path, committed: bool, sha, diff?, broken_links?, reason?}` _or_ `{error: "stale_base", ...}` | slow (LLM) | git+fts+triggers (only if committed) | Sync wrapper around `document_updater.run`. Loads the body, calls the LLM with the instruction, commits if the LLM returns a new body, returns `{committed: false, reason: "no_change"}` if it returned `NO_CHANGE`. Optional `base_sha` rejects stale writes before the LLM call. |

### Wiki filesystem ops

| Tool | Inputs | Returns | Latency | Writes | Notes |
| --- | --- | --- | --- | --- | --- |
| `move_path` | `old_path`, `new_path`, `commit_message` | `{old_path, new_path, sha, moved: [(old, new)]}` | fast | git (no fan-out) | Pure rename via `git mv`, single commit. Files **and** directories. Content unchanged → no trigger fan-out. FTS reindexes the new paths and drops the old ones. |
| `create_directory` | `path`, `commit_message` | `{path, sha, created: true}` | fast | git (no fan-out) | Empty folders aren't tracked by git, so this commits a `.gitkeep` marker inside. Rejects `.md` extensions and existing paths. |

### Triggers

Trigger CRUD is git-backed (`<dir>/.trigger_<id>*.yaml`) with the
Postgres `triggers` table as a denormalized cache for fan-out lookup. See
[natural-language-triggers](../natural-language-triggers/natural-language-triggers.md).

| Tool | Inputs | Returns | Latency | Writes | Notes |
| --- | --- | --- | --- | --- | --- |
| `create_trigger` | `scope_path`, `trigger_nl_condition`, `trigger_fire_message`, `destination?` (slug from `get_trigger_destinations`; defaults to `event_log`) | `{trigger_id, scope_path, ...}` | fast | git+db | Owned by the current user. Requires `read` ACL on `scope_path` — returns `{"error": "...read access..."}` otherwise. Destination validated against the `trigger_destinations` table. |
| `update_trigger` | `trigger_id`, optional `scope_path` / `trigger_nl_condition` / `trigger_fire_message` / `destination` / `enabled` | `{trigger_id, ...}` | fast | git+db | Partial update — omitted fields preserved. Ownership enforced. Re-checks `read` ACL on the final scope (new one if rebinding, otherwise existing) so revoked access blocks further mutation. |
| `get_trigger_destinations` | _none_ | `{destinations: [{id, name, description}]}` | fast | none | Catalog of where a trigger fire can be delivered. v0 ships only `event_log` (record to events table, no outbound dispatch); future destinations land via migration as their dispatchers come online. Call before `create_trigger` / `update_trigger` if the user wants to pick a destination. |

### Web

| Tool | Inputs | Returns | Latency | Writes | Notes |
| --- | --- | --- | --- | --- | --- |
| `web_search` | `query: str`, `num_results?: int` (≤20) | `[{title, link, snippet, published_date?}]` | slow (network) | none | Serper-backed. Use for things the wiki probably doesn't contain (current events, library docs). Prefer `search_wiki` first when relevant. |
| `open_urls` | `urls: list[str]` (1–10) | `{results: [{title, link, full_content, published_date?, scrape_successful}, ...]}` | slow (network) | none | Firecrawl-backed page fetch → markdown. Pass every URL to read in a single call (fetched concurrently server-side); don't issue parallel `open_urls` calls. If `scrape_successful` is false on a row, `full_content` may be empty — caller should not fabricate. |

## Cross-cutting contracts

### Optimistic concurrency (`base_sha`)

Write tools accept an optional `base_sha` argument and call
`assert_base_sha(rel, base_sha)` in `_doc_helpers.py`:

- `None` → skip the check (the caller has opted out).
- equal to current `HEAD` for `rel` → write proceeds.
- not equal → return `{error: "stale_base", base_sha, current_sha,
  message}` — same shape every write tool returns. The caller re-reads
  the doc, re-derives the edit against the new body, and retries.

`write_doc` makes `base_sha` **required** for overwrites of existing
files (`{error: "base_sha_required_for_overwrite"}` if missing) — full-
body overwrite has no fuzzy `old_string` chain to fall back on if HEAD
drifted. The other write tools (`edit_doc`, `multi_edit`, `apply_patch`,
`update_doc_nl`) treat it as optional; the agent typically passes it
when it has just read the doc and wants the write to fail fast on drift
rather than silently apply against a different body.

### Post-write side-effect chain

Every successful doc commit goes through
`commit_and_fan_out(rel, body, message, change_kind)` in
`app/llm/agents/tools/_doc_helpers.py`, which:

1. Calls `wiki_git.commit_file(rel, body, message, author=...)`.
2. Calls `wiki_notify.after_doc_write(rel, sha, change_kind, author)`.

`after_doc_write` is the **single seam** every successful `.md`
mutation flows through — both the chat-agent tools and the
`/api/documents/file` HTTP handlers call it. Today it queues an FTS
reindex (`tasks.reindex.reindex_path`) and fans out to the NL-trigger
evaluator (`tasks.triggers.fan_out_trigger_eval`). The planned MCP
resource pub-sub plugs in here too — see [seams.md](../seams.md) and
[mcp-server.md](../mcp-server/mcp-server.md).

`move_path` and `create_directory` deliberately bypass `after_doc_write`
— a rename has no diff to evaluate (so no trigger eval), and a
`.gitkeep` isn't a doc.

### Author identity on commits

`_doc_helpers.author_string()` reads `app.auth.current_user()` and
formats `"<name> <email>"` for the git commit author. Outside a request
context (tests, periodic tasks) it returns `None` and the git wrapper
falls back to its default identity (`agent-wiki@local`). The MCP layer
will set this from the bearer-token's user when it ships.

### Errors

Every handler returns `{"error": "<message>"}` instead of raising on
expected failures (bad input, missing file, fuzzy-match miss, LLM
provider error). Unexpected exceptions propagate to the loop and are
caught + stringified there. The model sees the error string verbatim
and self-corrects on the next iteration.

A handful of "structured errors" carry extra fields beyond `error`:

- `apply_patch` / `update_doc_nl` on stale base:
  `{error: "stale_base", base_sha, current_sha, message}`.
- `read_doc` on missing sha: `{error: "sha_not_found: ..."}`.

Callers (chat loop, MCP) treat these the same — feed back to the model.

## Sub-agents that own a tool

Three sub-agents are dispatched **from** tools:

| Sub-agent | Entry point | Triggered by | Pattern |
| --- | --- | --- | --- |
| `wiki_qa` | `app/llm/agents/wiki_qa.py:run` | `ask_nl_question` | One-shot `run_chat_loop` with `search_wiki` + `read_page` only, max 6 iterations. Returns synthesized answer + sources. |
| `document_updater` | `app/llm/agents/document_updater.py:run` | `update_doc_nl`, also (planned) pgmq doc-update task | Single `client.complete` call with the system+user prompts under `app/llm/prompts/`. Returns `None` on `NO_CHANGE` else the new body string. |
| `chat` (the user-facing one) | `app/llm/agents/chat.py:run_chat_stream` | `/api/chat/messages` HTTP endpoint | Multi-iteration tool-using loop; SSE-streamed back to the browser. |

`wiki_qa` lazy-imports `chat` to avoid the circular `chat → tools →
ask_nl_question → wiki_qa → chat` cycle. Don't move those imports back
to module top-level.

## Adding a new tool — checklist

1. Drop `<name>.json` (LLM-facing spec) and `<name>.py` (handler) into
   `backend/app/llm/agents/tools/`. Match `name`, filename stem, and
   module name exactly.
2. Reuse `_doc_helpers` for path validation, optimistic-concurrency
   check, commit + fan-out, and result assembly. Don't re-implement
   those.
3. If the tool writes, prefer `commit_and_fan_out` over calling
   `wiki_git.commit_file` directly — it's the seam that triggers
   reindex + trigger fan-out + (future) MCP pubsub.
4. Return `{error: "..."}` on expected failures. Reserve raises for
   programmer errors.
5. If the tool spawns a sub-agent (LLM call), put the sub-agent under
   `app/llm/agents/<name>.py` and lazy-import `chat` if it loops.
6. Add a row to this page in the same PR.
7. If the tool introduces a new "must go through here" rule (e.g. a
   new write seam), add a row to [seams.md](../seams.md).
