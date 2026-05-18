# Greptile Review Rules

## Type Annotations

Use explicit type annotations for variables to enhance code clarity, especially when moving type hints around in the code. Python files should be strict-typed; basedpyright runs in strict mode in CI.

## Best Practices

Use the root `CLAUDE.md` "Architectural rules" and "Frontend rules" sections as core review context. Prefer consistency with existing patterns, fix issues in code you touch, avoid tacking new features onto muddy interfaces, fail loudly instead of silently swallowing errors, keep code strictly typed, preserve clear state boundaries, remove duplicate or dead logic, break up overly long functions, avoid hidden import-time side effects, respect module boundaries, and favor correctness-by-construction over relying on callers to use an API correctly.

## TODOs

Whenever a TODO is added, there must be an associated name or ticket in the form `TODO(name): ...` or `TODO(1234): ...`.

## Debugging Code

Remove temporary debugging code before merging — stray `print()`, `console.log()`, dump-to-file, or scratch endpoints have no place on `main`.

## Hardcoded Booleans

When hardcoding a boolean variable to a constant value, remove the variable entirely and clean up all places where it's used rather than just setting it to a constant. Dead branches are noise.

## Architectural Seams — Honor the Boundaries

The root `CLAUDE.md` lists the interfaces that must NOT be bypassed:

- LLM calls only through `app/llm/client.py` (no direct `anthropic` / `openai` / `google.genai` imports outside `app/llm/providers/<name>.py`).
- Auth via `Depends(require_user)` / `current_user()`; no raw `request.session` reads outside `app/api/auth.py`.
- Wiki ACL via `app/wiki/acl.py` + `require_can`; never read/write `acl_entries` directly.
- DB: SQLAlchemy 2.0 ORM, repos return dicts. Pydantic, NOT `@dataclass`, for structured records.
- Wiki commits via `app/wiki/git.py`; never shell out to `git` elsewhere.
- Background work via the `pgmq`-backed queues in `app/tasks/queues.py`; no ad-hoc threading.
- Logging via `app.utils.logging.setup_logging`; no `print()`, no `logging.basicConfig`.
- Tracing via `app/tracing/`; never `import braintrust` outside that package.

Flag any new code that bypasses these seams.

## No Raw SQL Outside the Allowed Sites

Raw SQL is permitted only in `app/db/fts.py` (pg_textsearch operator) and `app/tasks/queue.py` (pgmq functions). Anywhere else, use the ORM session — `session.execute(text(...))` outside those files is a regression to flag.

## Frontend — Design Tokens + Components

- No raw hex colors, radii, or shadows in React components — they live in `frontend/src/lib/theme.ts`. If a shade isn't there, add it there first.
- No `background: "white"` (or any literal); use `color.bg.page`.
- One `Button` component per app surface; new launcher-area components prefer `@onyx-ai/opal/components` (`Button`, `Tag`). Don't roll a new bespoke `<button style={{...}}>` for primary/secondary/danger chrome.
- Modal scrims use `color.overlay`; modal shadow uses `shadow.modal`. Don't introduce slate-tinted or pure-black scrims.

## Network — Only via `apiFetch`

`apiFetch` from `@/lib/api` is the only allowed seam for talking to the backend from the frontend. Raw `fetch(...)` to internal endpoints loses the `credentials: "include"` + JSON parsing + `ApiError` envelope.

## Auth — Only via the Provider

Pages call `useRequireAuth()` to gate and `useAuth()` to read state. Don't call `/api/auth/me` from a component — the provider owns it.

## Comments — Describe Current Behavior

Code comments describe what the code IS, not what it replaced or used to be. No dates, no PR numbers, no audit-reference tokens (`AF#X`, `R2#7`, `P1 #3`). Migration / refactor context belongs in commit messages, not source comments.

## Pre-Commit Discipline

The repo's pre-commit hooks (ruff + basedpyright strict, plus frontend typecheck in CI) are the authority. Code that lints clean locally but trips them on push is a regression. Run `pre-commit run --files <changed>` before requesting review.
