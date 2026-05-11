# Integration tests

> **Scope.** This page is the rulebook for tests under
> `backend/tests/integration/`. Unit-test patterns (fixtures, seam
> mocking, naming) live in `CLAUDE.md`'s Testing section.

_Last updated: 2026-05-08_

Integration tests exercise full request-to-side-effect flows against
the real Postgres (per-test schema), the real wiki git repo, the real
FastAPI app, and the real pgmq queues. The only seam that's stubbed is
the LLM — `app.llm.client.complete` and `stream` are routed through a
scripted mock so test runs don't hit a provider and don't depend on
keys.

## Layout

```
backend/tests/
  conftest.py                 # tmp_config / tmp_db / tmp_repo (shared)
  integration/
    __init__.py
    conftest.py               # immediate_queues, mock_llm, client, integration
    test_smoke.py             # foundation smoke — keep passing
    test_<flow>.py            # one file per flow you're locking in
```

## What you get from the harness

The `integration` fixture is the one-stop entry point. It composes:

| Underlying fixture | What it gives you |
| --- | --- |
| `tmp_repo` | Per-test Postgres schema with `init_db()` already run + an initialized wiki git repo on disk. |
| `immediate_queues` | All three `TaskQueue` instances run handlers inline — `reindex`, `fan_out_trigger_eval`, `cleanup_expired_activity` all execute in the request thread. No polling. |
| `mock_llm` | `app.llm.client.complete` and `stream` patched with a scripted mock. Tests script responses via `llm.respond(...)`; unscripted calls return a benign empty answer. |
| `app` / `client` | `app.main.create_app()` against the per-test schema, wrapped in FastAPI's `TestClient`. |

Don't pull in the underlying fixtures by hand unless you need to skip
one of them — for example, a test that's **about** the queue's
visibility-timeout behavior would request `client` and `mock_llm`
without `immediate_queues` so it actually hits pgmq.

## The harness API

```python
def test_my_flow(integration):
    # auth
    integration.signup_and_signin(email="u@x.com")
    integration.signin(email="u@x.com")          # reuse existing user
    integration.signin(user_id=existing_id)       # bypass /signup

    # docs
    integration.put_doc("guide.md", "# Guide\n\nbody")
    integration.delete_doc("guide.md")

    # triggers
    tid = integration.create_trigger(
        scope_path="status.md",
        condition="status changes",
        message="status flipped",
    )

    # event log
    fires = integration.fired_triggers()          # GET /api/events?kind=trigger.fire
    every  = integration.events(limit=200)        # newest-first

    # raw client for anything not covered
    integration.client.post("/api/documents/ingest", json={...})
```

The helpers go through real HTTP routes — they don't shortcut into the
domain layer. If a flow you need to test has no shortcut here, use
`integration.client` directly rather than reaching past the API seam.

## LLM mocking

The mock lives in `tests/integration/conftest.py:MockLLM`. It records
every `complete` / `stream` call and lets tests register canned
responses keyed by predicate.

```python
# Match every call (registered as a fallback).
integration.llm.respond(text="ok", stop_reason="end_turn")

# Match calls whose serialized messages contain a regex.
integration.llm.respond_match("matches the rule", text='{"matched": true, "reason": "x"}')

# Match by arbitrary predicate over the captured call dict.
integration.llm.respond(
    when=lambda c: any("urgent" in str(m) for m in c["messages"]),
    text="emergency response",
)

# Tool calls — pass them through `tool_calls`.
integration.llm.respond(tool_calls=[{
    "id": "tc_1",
    "name": "edit_doc",
    "arguments": {"path": "x.md", "old_string": "...", "new_string": "..."},
}])
```

After the action runs, inspect `integration.llm.calls` — a list of
`{messages, tools, max_tokens, model}` dicts in call order — to assert
on what the system asked the LLM.

**Default behavior**: a call with no matching script returns
`{"text": "", "tool_calls": [], "stop_reason": "end_turn", ...}`.
This is benign for the trigger evaluator (it parses an empty text as
"no match") and the chat loop (it ends the turn). Tests that depend on
specific text **must** script it — don't rely on the default.

**Streaming surface**: `respond(text=..., tool_calls=[...])` covers
both `complete` and `stream`. The stream version yields one
`text_delta`, then any `tool_call`s, then a `done`.

## Per-test isolation

| What | How it's isolated |
| --- | --- |
| Postgres state | `tmp_config` creates a unique schema (`test_<hex>`) per test, sets `search_path` via libpq `options=`, drops `CASCADE` on teardown. The engine cache is reset in conftest entry/exit so each test rebuilds against its schema. |
| Wiki git repo | `tmp_repo` initializes a fresh git repo under `tmp_path`. |
| pgmq queues | `pgmq.q_<name>` lives in the global `pgmq` schema, not the per-test schema, so messages can leak across tests. With `immediate_queues` (the default for `integration`) no real messages are sent and this doesn't matter. If you opt out, manually `TRUNCATE pgmq.q_<name>` between tests. |
| Module state | Anything that captures `from app.config import CONFIG` at import time is monkeypatched per-fixture — see `tmp_config` for the list. Add new bindings there if a future module pulls one in. |

## Conventions

- One file per flow (`test_signup_flow.py`, `test_doc_ingest_flow.py`,
  `test_trigger_fanout_flow.py`). Multiple `test_*` functions inside,
  each testing one branch.
- Use `integration.client.post(...)` for any HTTP route the harness
  doesn't wrap. Don't add a wrapper to the harness for a one-off
  call — it's noise. Add one when three tests start repeating the
  same boilerplate.
- Don't patch the LLM at the SDK level (`anthropic.Anthropic`,
  `openai.Client`). Always at `app.llm.client`. The provider modules
  are excluded from integration runs by design.
- Don't mock the database. Per-test schema is the isolation primitive.
- When asserting on background work, don't `time.sleep`. With
  `immediate_queues`, the side effect has already happened by the time
  the request returns.

## Running

```bash
cd backend
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/agent_wiki \
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/agent_wiki_test \
.venv/bin/pytest tests/integration -q
```

The test database (default `agent_wiki_test`) must already exist with
`pg_textsearch` and `pgmq` installed; spin up the compose Postgres
service and run:

```bash
docker compose up -d postgres
psql -h localhost -U postgres -d postgres -c 'CREATE DATABASE agent_wiki_test'
psql -h localhost -U postgres -d agent_wiki_test -c 'CREATE EXTENSION pg_textsearch; CREATE EXTENSION pgmq;'
```

Per-test schemas live inside that database; `tmp_config` creates and
drops them.

## When this framework isn't enough

- **Real LLM responses** (eval-style tests) — out of scope for this
  framework. Build them on top of the same fixtures, but pull
  `mock_llm` out of the `integration` composition so calls hit the
  provider. Mark them `@pytest.mark.live_llm` so they don't run by
  default.
- **Real queue interaction** — request `client` and skip
  `immediate_queues`. Drive the consumer in-thread by calling
  `app.tasks.queue.run_consumer(queue, ...)` from a daemon thread, or
  shape the test around the deferred case explicitly.
- **Multi-process behavior** (worker scaling, leader election for
  periodic schedulers) — out of scope. Test the single-process
  invariants here; revisit when sharding lands.
