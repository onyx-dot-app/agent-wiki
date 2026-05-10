# Integration test plan — flow coverage

> **Scope.** Concrete flows we want locked in under
> `backend/tests/integration/`. The harness rulebook lives in
> `integration-tests.md`; this page tracks **which flows are covered,
> which are runnable today, and which depend on unfinished features.**

_Last updated: 2026-05-08_

## Running the suite

Same recipe as the rest of `tests/integration/` — see
`integration-tests.md` for the full setup. Quick form:

```bash
cd backend
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/agent_wiki \
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/agent_wiki_test \
.venv/bin/pytest tests/integration -q
```

`agent_wiki_test` must already exist with `pg_textsearch` and `pgmq`
installed; per-test schemas live inside it. Expected today:
**15 passed, 1 skipped** (the skip is Flow 4).

## Shared isolation rules

All four flows below run inside the `integration` fixture
(`tests/integration/conftest.py`):

- **Postgres** — per-test schema via `tmp_config`; no cross-test bleed.
- **Wiki repo** — fresh tmp git repo per test via `tmp_repo`.
- **Queues** — `immediate_queues` flips every `TaskQueue.immediate = True`,
  so handlers (`reindex_path`, `fan_out_trigger_eval`,
  `process_pushed_document`) run inline in the request thread. **No
  real pgmq messages are sent**, which is what keeps tests from
  interfering with each other (pgmq tables live in the global `pgmq`
  schema, not the per-test schema).
- **LLM** — `mock_llm` patches `app.llm.client.complete` / `stream`
  (and the captured reference in `app.triggers.natural_language`).
  Tests script tool-call responses; unscripted calls return a benign
  empty answer.

What stays **real**: Flask app, auth, repos, git wrapper, BM25 indexer,
trigger evaluator wiring, event log writer.

---

## Flow 1 — Doc-scoped trigger fires on edit

**Status: ✅ covered.** Exactly the smoke test
`test_trigger_fires_when_llm_says_match` in
`tests/integration/test_smoke.py`; the flow below is the canonical
shape we should keep adding variants of.

**Steps**

1. `signup_and_signin()`.
2. `put_doc("status.md", "# Status\n\nstatus: green\n")` — create at root.
3. `create_trigger(scope_path="status.md", condition=..., message=...)`.
4. Script the LLM seam deterministically:
   - Phase 1 (`report` tool) → `{matches: True, reason: ...}`.
   - Phase 2 (`render` tool) → `{message: "..."}`.
5. `put_doc("status.md", "# Status\n\nstatus: yellow\n")` — matching edit.
6. Assert `integration.fired_triggers()` contains a `trigger.fire`
   event with the rendered message.

**What's mocked.** Only the LLM. Match/no-match is deterministic
because the test scripts both phases.

**What's real.** Trigger creation writing the YAML side-file
(`app/triggers/storage.py`), `Trigger` row insert, fan-out task
(`fan_out_trigger_eval`) running inline, event log row.

---

## Flow 2 — Folder-scoped trigger fires on create + edit

**Status: ✅ covered.** Lives in
`tests/integration/test_folder_trigger_flow.py`. Both code paths
exercised:

- `app/triggers/engine.py:evaluate_new_file_in_dir` — combined
  eval+render path used when a brand-new file appears under a
  dir-scoped trigger.
- `app/triggers/engine.py:evaluate_delta` — standard delta path used
  when an existing file under the dir gets edited.

`find_matching_triggers` walks `parent_dirs(doc_path)`, so a trigger
with `scope_path="reports"` matches `reports/foo.md`.

**Steps**

1. `signup_and_signin()`.
2. `client.post("/api/documents/folder", json={"path": "reports"})` —
   create a folder at root. (No harness helper today; use the raw
   client, or add `mkdir(path)` to `IntegrationHarness` if a second
   test needs it.)
3. `create_trigger(scope_path="reports", condition=..., message=...)` —
   directory scope (no `.md` suffix → `kind_of_scope` returns `dir`).
4. Script the LLM by **call shape**, not tool name:
   - new-file path → `complete()` is called with **no tools**, expects
     a JSON object in the assistant text:
     `{"triggered": bool, "trigger_message": str}`. Script with
     `respond(when=lambda c: not c.get("tools"), text=json_body)`.
   - edit path → standard delta: script `report` + `render` tool calls
     keyed by `tool name in tools`, same as Flow 1.
5. `put_doc("reports/q1.md", "...matching content...")` — first
   create. Assert `fired_triggers()` has 1 event with
   `change_kind == "create"`.
6. `put_doc("reports/q1.md", "...different matching content...")` —
   edit. Assert `fired_triggers()` has 2 events (newest-first), the
   new one with `change_kind == "edit"`.

**Determinism note.** The mock dispatches by predicate; script the
new-file response by "no tools" and the edit phases by tool name.

---

## Flow 3 — BM25 index picks up a new doc

**Status: ✅ covered.** Lives in
`tests/integration/test_bm25_indexing_flow.py` (broken out from
`test_signup_and_save_doc_and_search` so the index path is asserted on
its own); also exercised end-to-end by the smoke test.

**Steps**

1. `signup_and_signin()`.
2. `put_doc("guide.md", "# Bcrypt Guide\n\nwe use bcrypt for password hashing\n")`.
3. With `immediate_queues`, `reindex_path` has already run by the
   time the PUT returns.
4. `from app.db import fts; hits = fts.search("bcrypt")`.
5. Assert at least one hit with `path == "guide.md"` and a snippet
   containing the query term wrapped in `**…**`.

**What's mocked.** Nothing on the index path. The LLM seam is mocked
globally by `mock_llm` but isn't on this path at all — BM25 indexing
doesn't call the model.

---

## Flow 5 — Agent read stamps the doc's `agents:` frontmatter

**Status: ✅ covered.** Lives in
`tests/integration/test_frontmatter_flow.py`. Asserts that a
``read_page`` invocation upserts a row in the agent-activity registry
and re-renders the doc body so the committed file carries an
``agents:`` block listing the user, the per-turn ``agent_name_var``,
``activity: read``, and an ``expires_at`` ~24h in the future.

**Steps**

1. `signup_and_signin()`.
2. `put_doc("guide.md", "# Guide\n\noriginal body\n")` — no frontmatter
   yet on disk.
3. Inside `flask_app.test_request_context()` with
   `session["user_id"]` set, set `agent_activity.agent_name_var` to a
   per-turn name, then call `app.llm.agents.tools.read_page.handle({...})`.
4. Read the doc back via `app.wiki.git.read_file` and assert the
   leading frontmatter contains the expected `owner`, `agent`,
   `activity`, and a future `expires_at`.

**Caveat — eta in immediate mode.** `mark_doc_read` schedules a 24h
cleanup that deletes the row and re-renders the frontmatter. Under
`immediate_queues` the eta is ignored and the cleanup fires
synchronously, wiping the stamp before assertions run. The test
monkeypatches `app.tasks.agent_activity.schedule_cleanup_for_natural_key`
to a no-op for the duration. In production the eta keeps the cleanup
pending; this is purely an artifact of the test harness.

**What's mocked.** Only the cleanup scheduler (and the global LLM
seam, which this path doesn't exercise).

**What's real.** `read_page` handler, `mark_doc_read`, the
agent-activity DB upsert, frontmatter render
(`agent_activity.replace_frontmatter`), the second commit through the
real git wrapper, BM25 reindex.

---

## Flow 4 — External ingest updates a wiki doc via the document-updater agent

**Status: ⛔ NOT runnable yet — feature incomplete.** Placeholder
test exists at `tests/integration/test_doc_ingest_flow.py` marked
`@pytest.mark.skip` pointing at `process_pushed_document`. Drop the
skip marker once the consumer lands.

The HTTP entry point exists (`POST /api/documents/ingest` →
`app/api/documents.py`) and validates + enqueues
`process_pushed_document` on `documents_queue`. But the consumer is a
stub:

- `app/tasks/document_update.py:process_pushed_document` —
  `raise NotImplementedError`.
- `app/tasks/document_update.py:update_document_from_payload` —
  `raise NotImplementedError`.

So the ingest endpoint queues a task that immediately blows up under
`immediate_queues`. Until the task lands, this test should be
authored as `@pytest.mark.skip(reason="document-updater task not
implemented; see app/tasks/document_update.py")` or omitted.

**Intended steps (write them now, leave skipped)**

1. `signup_and_signin()`.
2. `put_doc("topics/auth.md", "# Auth\n\noriginal body about
   sessions\n")` — pre-existing wiki page.
3. Script the document-updater LLM:
   - Routing call → tool call selecting `topics/auth.md`.
   - Edit call → tool call producing an updated body.
4. `client.post("/api/documents/ingest", json={"content": "...new info
   about token rotation...", "source_type": "test", "title":
   "Auth notes"})` — expect `202`.
5. With `immediate_queues`, the doc-updater task runs inline, commits
   via `app/wiki/git.py`, re-enqueues `reindex_path` and
   `fan_out_trigger_eval`.
6. Assert the doc body changed (`app.wiki.git.read_file`), the BM25
   index reflects the update, and an event row records the agent edit.

**What blocks this.** The three TODOs in `process_pushed_document`:
LLM-routed page resolution, agent invocation, and the
commit + reindex + fan-out side effects. Once those are written, this
flow drops in with no harness changes — the LLM seam, queue mode, and
event-log helper already exist.

---

## Negative flows

Five negative tests sit alongside the positive ones, picked because
their visible failure mode in production would be either silently
spurious notifications or a swallowed worker exception. Each asserts
on absence (no fire, no commit) plus, where it matters, the call
shape of the LLM seam.

`tests/integration/test_trigger_negative_flow.py`:

* **`report` returns `matches=False`** — eval ran, render never did,
  no `trigger.fire` row.
* **Disabled trigger** — `enabled=false` short-circuits in
  `find_matching_triggers`; LLM never called at all.
* **`LLMError` during phase 1** — provider failure is swallowed; PUT
  still commits, no event row, no exception escapes the worker.
* **Unparseable new-file-in-dir text** — junk JSON is dropped, not
  fired.

`tests/integration/test_doc_tamper_flow.py`:

* **Frontmatter tamper** — agent submitting a body that mutates the
  registry-managed `agents:` block gets a `ToolError`; disk + head
  sha unchanged.
* **Read-before-write** — write tool refuses to overwrite a doc not
  in `seen_doc_paths`; positive complement asserts the same write
  goes through once the path is registered.

`MockLLM.raise_for(exc, when=...)` simulates provider failures
without patching the seam itself — needed for the `LLMError` path.

## Adding to the harness

These four flows don't need new fixtures. If a future flow does:

- Folder creation deserves a helper (`integration.mkdir(path)`) once
  more than one test uses it. Until then, raw `client.post` is fine.
- For Flow 4, no harness change is needed — script the LLM by tool
  name like Flows 1 and 2.
- Resist adding domain-shortcut helpers (e.g. "directly insert a
  trigger row"). The harness's job is to drive real HTTP routes; that
  is what makes these tests integration tests.
