# Tool Design

How the chat agent's wiki-editing tools are designed, and why. Source of
truth for the function-call surface: spec JSONs in
`backend/app/llm/agents/tools/`. Source of truth for the editing primitives:
`backend/app/wiki/edit.py` and `backend/app/wiki/links.py`.

## Problem

The chat agent needs to update wiki docs on the user's behalf. A naive
"overwrite the whole file" tool is wasteful (long docs, tiny changes burn
tokens) and brittle (the model can drift from the canonical body). We want
something closer to a real code-editor: surgical find-and-replace for small
changes, full-body writes only when justified, and protection against
blind edits.

## Reference: opencode (`~/Projects/opencode`)

We modeled this on opencode's tool layer. Highlights of their design
(file paths refer to `packages/opencode/src/tool/`):

### Three write tools, split by intent

| Tool | Purpose | Schema |
| --- | --- | --- |
| `write` | Full-body overwrite or new file | `filePath`, `content` |
| `edit` | Surgical find-and-replace | `filePath`, `oldString`, `newString`, `replaceAll?` |
| `multiedit` | Sequential atomic edits to one file | array of edits |
| `apply_patch` | Unified-diff envelope (GPT-4 only) | `*** Begin Patch ... *** End Patch` |

### Read-before-write enforcement

`FileTime.assert(sessionID, filePath)` refuses to write if the model never
read the file in this session (or if `mtime` is newer than the recorded
read time). The error goes back to the model: `"You must read file X
before overwriting it"`. Forces the model to ground itself in current
contents before proposing changes.

### Fuzzy replacer chain

`edit.ts` tries 9 replacer strategies in order until one finds a unique
match:

1. **`SimpleReplacer`** — exact substring match.
2. **`LineTrimmedReplacer`** — match line-by-line, ignoring leading/trailing whitespace per line.
3. **`BlockAnchorReplacer`** — match first and last lines of the block exactly; score middle lines with Levenshtein. Single candidate accepted at any similarity ≥ 0; multiple candidates require similarity ≥ 0.3.
4. **`WhitespaceNormalizedReplacer`** — collapse all whitespace runs to single spaces, match.
5. **`IndentationFlexibleReplacer`** — strip the minimum common indent from both sides, match.
6. **`EscapeNormalizedReplacer`** — unescape `\n`, `\t`, etc. on both sides, match.
7. **`TrimmedBoundaryReplacer`** — trim leading/trailing whitespace of the search, then match.
8. **`ContextAwareReplacer`** — match first/last lines as anchors; require ≥ 50% of middle non-empty lines to match exactly when trimmed.
9. **`MultiOccurrenceReplacer`** — yield every exact match (used for `replaceAll`).

The point of the chain: real-world models often produce `oldString` with
slightly wrong indentation or stale whitespace. Block-anchor with
Levenshtein lets the edit succeed without making fuzzy match unsafe — the
first and last lines must match exactly, only the middle is fuzzy-scored.

After a candidate is yielded, the outer `replace()` loop picks it iff it
appears exactly once in the file; otherwise it tries the next replacer or
errors with `"Found multiple matches"` so the model can retry with more
context.

### Per-file write lock

`FileTime.withLock(filePath, fn)` serializes concurrent writes so two
agent calls can't interleave on the same file.

### Diff-attached permission ask

Before writing, opencode computes the unified diff, then
`ctx.ask({permission: "edit", patterns: [path], metadata: {diff}})`. The
approver sees the diff at decision time. Model gets `"permission denied"`
back if the user rejects.

### Post-edit feedback (LSP)

After a successful write, opencode runs `LSP.diagnostics()` and returns
errors back to the model in `<diagnostics file="...">` tags so it can
self-correct without another round-trip.

### Tool descriptions in separate files

Each tool ships a `<name>.txt` (e.g. `edit.txt`) imported at the top of
the implementation file. Same audit-friendly pattern we already adopted
with `<name>.json`.

## Our adaptation

### What we're keeping (verbatim or near-verbatim)

- **The three-tool split.** `write_doc`, `edit_doc`, `multi_edit`. We're
  skipping `apply_patch` — unified-diff format is hard for models to
  produce correctly without strong training signal, and the fuzzy fallback
  chain gets us 95% of the wins.
- **The full fuzzy replacer chain (all 9 strategies).** Ported into
  `backend/app/wiki/edit.py`. Self-contained, no external deps.
- **Read-before-write.** Adapted: we have no general read tool, so the
  read source is `search_wiki` — the chat loop tracks which paths the
  model has seen via search results in a `ContextVar`, and the write tools
  refuse edits to unseen paths.
- **Atomic semantics for `multi_edit`.** All edits applied in memory
  against the read body; only commit if every edit succeeds. One edit
  failure → no commit.
- **Spec-in-separate-file convention.** Already in place (`<name>.json`).
- **Diff in tool result.** Each write tool returns the unified diff in
  its result so the chat UI can render the "Apply / Reject" preview
  without re-reading the file.
- **LSP for markdown.** No real LSP, but we run a broken-wiki-link check
  after every write and surface broken links back to the model — same
  feedback-loop shape as opencode's LSP integration.

### What we're skipping

- **`apply_patch`.** Add only if `edit_doc` proves insufficient.
- **Per-file write lock.** Git's working-tree write is the lock; if two
  edits race, the second commits over the first as a separate commit, and
  the model can resolve via search + edit. No need for in-process locks.
- **Mtime/staleness checks.** Git is the source of truth. If the model's
  `oldString` no longer matches HEAD because someone else committed, the
  edit just fails ("oldString not found") and the model resyncs via
  `search_wiki`.
- **Snapshot tracking (`{before, after, additions, deletions}`).** Git
  history gives us this; no need for parallel state.
- **Hard permission gate.** The "Apply / Reject" UI in the product spec
  belongs in the frontend (chat panel renders the diff, user clicks
  Apply). At the tool layer, we keep the soft gate: the system prompt
  instructs the model to confirm with the user before calling write/edit
  tools. Tighten later when the frontend gate ships.

## Tool set

### `write_doc(path, body, commit_message)`

Full-body overwrite or create. Use only for new docs or wholesale
restructures (>50% of lines changing).

- Validates `path` via `safe_rel_path`, requires `.md` extension.
- If the file exists, requires `path` to be in this session's
  `seen_paths` (i.e. the model called `search_wiki` and saw it). New
  files are exempt.
- Commits via `wiki_git.commit_file`; queues `reindex_path` and
  `fan_out_trigger_eval`.
- Returns `{path, sha, created, diff, broken_links?}`.

### `edit_doc(path, old_string, new_string, replace_all?, commit_message)`

Find-and-replace one occurrence (or all) using the fuzzy replacer chain.

- Validates `path` and `seen_paths` (file must exist).
- `old_string == new_string` → error.
- Calls `wiki.edit.replace(body, old_string, new_string, replace_all)`.
- On match: write + commit + reindex + fan-out.
- On no-match: returns `{"error": "old_string not found"}`. On multiple
  matches without `replace_all`: returns
  `{"error": "old_string matched multiple times — provide more context or pass replace_all=true"}`.
- Returns `{path, sha, diff, broken_links?}`.

### `multi_edit(path, edits, commit_message)`

Atomic batch of `edit_doc`-shaped operations on one file.

- Each edit: `{old_string, new_string, replace_all?}`.
- Applies sequentially in memory: the result of edit `n` is the input to
  edit `n+1`.
- If any edit fails (no match, multi-match, or empty `old_string`), the
  whole batch aborts; nothing is written or committed.
- Single commit, single reindex, single fan-out.
- Returns `{path, sha, diff, applied_count, broken_links?}`.

### `create_directory(path, commit_message)`

Make an empty wiki folder. Git doesn't track empty dirs, so the tool
commits a `.gitkeep` marker inside (mirrors the `POST /api/documents/folder`
endpoint humans use from the explorer).

- Validates `path` via `safe_rel_path`; rejects `.md` extension and any
  path that already exists as a file or directory.
- Same approval rule as the write tools: only call after the user
  explicitly asks for the folder or confirms when proposed.
- Returns `{path, sha, created}`.

### `move_path(old_path, new_path, commit_message)`

Pure rename of a file or directory in one commit. Wraps `git mv`, so a
folder rename moves every tracked file inside it and history follows
through `git log --follow`.

- Validates both paths via `safe_rel_path`. Rejects identical
  source/target, missing source, existing target, and shape mismatches
  (e.g. `.md` → non-`.md`).
- Content is unchanged, so there's no read-before-write check — moves
  don't risk a blind overwrite. Same approval rule as the other write
  tools.
- After commit, every moved `.md` is removed from FTS at its old path
  and queued for reindex at the new path. No trigger fan-out (a rename
  has no diff to evaluate).
- Returns `{old_path, new_path, sha, moved: [{old, new}, ...]}`.

## Read-before-write tracking

A `ContextVar` (`backend/app/llm/agents/_session.py`) holds a `set[str]`
of paths the model has actually read this turn. The chat loop:

1. Sets the var to an empty set on entry, resets on exit.
2. After every `read_page` tool call, adds the returned `path` to the set.

`search_wiki` does **not** populate the set — its ~64-token snippets are
discovery aids, not full reads, and aren't enough context to safely edit.
The flow we want the model to follow is `search_wiki → read_page → edit`.

The write tools read the var. If the var is `None` (called outside a
chat loop, e.g. tests, direct invocation), the check is skipped. If the
var is a set and the target path isn't in it AND the file already exists,
the tool returns:

```
{"error": "You must call search_wiki for '{path}' (or a query that returns it) before editing it"}
```

This protects against "model hallucinates the wiki" failures without
adding a separate filesystem read tool — `read_page` is the read seam.

## Broken-link check (markdown LSP analogue)

After every successful write, we scan the new body for markdown links
matching `[text](relative/path.md)` (or `[text](relative/path.md#anchor)`).
For each, we resolve relative to the doc's directory and check existence
in the wiki working tree. Any broken targets are returned in the tool
result as `broken_links: [{from, target}, ...]` so the model can fix them
in a follow-up edit without another round-trip.

We don't fail the write on broken links — they may be intentional
forward references — we only surface them.

## Code organization

```
backend/app/wiki/
  edit.py             # Fuzzy replacer chain + replace() — pure logic, no I/O
  links.py            # find_broken_links(body, doc_path) -> list[BrokenLink]
  git.py              # (existing)
  filesystem.py       # (existing)
  search.py           # (existing)

backend/app/llm/agents/
  _session.py         # ContextVar for seen_paths
  chat.py             # Sets the var; populates after search_wiki
  tools/
    __init__.py       # registry (existing)
    search_wiki.{json,py}
    create_trigger.{json,py}
    write_doc.{json,py}
    edit_doc.{json,py}
    multi_edit.{json,py}
    create_directory.{json,py}
    move_path.{json,py}
```

`backend/app/wiki/edit.py` lives next to `git.py` because it's a wiki-edit
primitive, not a tool concern. The tools call into it. This keeps the
tools thin (validate args, check seen_paths, call wiki primitives, format
result) and the editing logic reusable (e.g. by the document-updater
agent later).

## Testing

- `tests/test_wiki_edit.py` — unit tests per replacer + integration tests
  on `replace()`. Cover the canonical cases from opencode's source
  comments (whitespace drift, indentation drift, ambiguous match,
  no match, multi-match without `replaceAll`).
- `tests/test_wiki_links.py` — broken-link detection on synthetic markdown.
- `tests/test_tools_doc_edit.py` — end-to-end tool tests against a tmp
  wiki repo: `seen_paths` enforcement, atomic `multi_edit` rollback,
  reindex/fan-out side effects.

## Trigger tools

`create_trigger` and `update_trigger` work the same three-part trigger
shape:

- **`trigger_nl_condition`** — the **if** condition (NL string evaluated
  by an LLM against each diff). Stored on the trigger row as
  `nl_description`.
- **`trigger_fire_message`** — what notification body to deliver when the
  trigger fires. Stored on the trigger row as `message`.
- **`destination`** — slug of a row in the `trigger_destinations`
  catalog table. Defaults to `"event_log"` (the only seeded row in v0),
  which routes the fire to the **Event Log**: a `trigger.fire` row is
  inserted into the `events` table with the `message` carried in the
  payload. Validation goes through `app/triggers/destinations.py:exists`,
  shared by the API, repo, and the LLM agent tools — adding a new
  destination is a one-line migration plus a dispatcher branch in
  `_record_fire`. The catalog itself is exposed to the chat agent via
  the `get_trigger_destinations` tool.

Storage:

- The two new fields live in the existing `triggers.action_json` column
  as a JSON blob `{"message": "...", "destination": "event_log"}`. No
  schema migration of the `triggers` table was needed.
- The YAML on disk gets two new top-level keys (`message`, `destination`).
- `storage.parse` is tolerant of pre-existing YAML files that lack these
  keys (they default to `None`); `_parse_action` in the repo does the
  same for old Postgres rows. Both then run through
  `repo._normalize_destination` which maps legacy `None` → `"event_log"`
  so callers see one shape across the migration boundary.

Fan-out (`app/tasks/triggers.py:_record_fire`):

- Pulls `message` + `destination` out of the trigger's `action_json`.
- For `destination == "event_log"`, writes the standard `trigger.fire`
  event, including `message` and `destination` in `payload_json` so the
  Events UI can render the message.
- For any other destination id, logs a warning and falls through to the
  Event Log so no fire is lost. Once outbound dispatchers ship they will
  branch off here, keyed on the destination id.

`update_trigger` accepts partial updates: pass the `trigger_id` plus any
subset of `scope_path`, `trigger_nl_condition`, `trigger_fire_message`,
`destination`, or `enabled`. Ownership is enforced (a user may only update triggers they
own). The repo's `update` uses an `_UNSET` sentinel internally so we can
tell "destination omitted" apart from "destination explicitly set to
null."

## Bash tool (`run_bash`)

Ported from `EnterpriseRAG-Bench/src/scripts/answer_generation/agent_retrieval.py`
with our adaptations. Lives in `backend/app/llm/agents/tools/_bash.py`
(pure logic) + `tools/run_bash.{json,py}` (spec + thin handler).

Two-layer architecture (matches the upstream):

- **Execution layer** — `parse_chain` tokenizes the command string respecting
  quotes, then `execute_chain` runs each segment via `subprocess.run` with
  `shell=True`. Stdout pipes into the next segment's stdin. `&&` / `||` /
  `;` semantics are honored by inspecting return codes between segments.
  Per-segment timeout is 30s; binary output on the final segment aborts
  with `[error] binary file detected`.
- **Presentation layer** — `format_output` applies a 100-line cap when the
  chain ends in `grep`/`rg`/`find`, then a generic 2 000 lines / 50 KB cap.
  Truncation is signalled via `truncated: true` in the result.

Adaptations from the upstream port:

- **cwd is pinned to `CONFIG.wiki_dir`.** The model explores the wiki
  working tree, not the Flask source. Looked up at call time so test
  fixtures that monkeypatch `app.config.CONFIG` flow through.
- **Allowlist is `{cat, find, grep, ls, head, tail, wc}`.** Read-only
  only — no `rm`, `mv`, `cp`, `git`, `sh`/`bash`/`python`, no shell
  redirection. Writes go through `edit_doc` / `write_doc` / `multi_edit`
  / `move_path` / `create_directory` so they're committed and audited.
- **Whitelist is checked upfront.** `validate_chain` runs against every
  parsed segment immediately after `parse_chain`, before any subprocess
  fires (the upstream only checks the first segment, which lets
  `ls | xargs rm`-style smuggling through). One gate, one place.
- **`||` short-circuit preserves the lhs output** — the upstream port
  drops it when the lhs succeeds; we keep it because that's what the
  user usually wants.
- **Skipped:** per-session repeat-detection, zero-result subdirectory
  hints, semaphore-based concurrency gate, deadline crediting. All
  designed for the upstream's batch-eval loop and unnecessary here.

The result shape is `{output, exit_code, elapsed_ms, truncated}`. Plain
text output (`output`) is what the LLM actually reads — no JSON envelope
in the body. Errors from the allowlist gate, timeouts, or stderr come
through with `exit_code != 0` and a leading `[error]` / `[stderr]` tag
in `output`.

System-prompt framing: `run_bash` is the **backup** tool. Reach for it
only when the user is asking a wiki-related question and the other
tools (`search_wiki`, `read_page`, etc.) don't give the model enough
flexibility — e.g. counting markdown files in a directory, listing the
tree, finding a literal string across the whole wiki with line numbers,
scanning many docs in one pass. Normal content lookups still go through
`search_wiki` + `read_page`; doc mutations still go through the doc-edit
tools.

## Open questions

- **Should `edit_doc` accept a hint about which match the model wants
  when there are multiples (e.g. line number, occurrence index)?**
  Opencode just errors and asks for more context. We follow suit;
  revisit if the error rate is high in practice.
- **Should `write_doc` block creating files at `<wiki>/.triggers/...`?**
  That's where trigger YAML lives — agents shouldn't write there directly.
  TODO: have `safe_rel_path` (or a new check) reject `.triggers/` from
  the doc tools.
- **Trigger destination registry.** The catalog now lives in the
  `trigger_destinations` table (`id, name, description, created_at`) and
  is surfaced to agents via `get_trigger_destinations`. v0 ships only
  `event_log`. When the second destination type arrives (webhook /
  Slack / agent message), drop a migration insert into
  `trigger_destinations` and add the dispatcher branch in
  `tasks/triggers.py:_record_fire`. The creation surface (`create_trigger`,
  `update_trigger`, REST API) doesn't need to change — validation is
  catalog-driven.
