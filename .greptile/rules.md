# Greptile Review Rules

## Type Annotations

Use explicit type annotations for variables to enhance code clarity, especially when moving type hints around in the code. Python files should be strict-typed; basedpyright runs in strict mode in CI.

## Best Practices

Prefer consistency with existing patterns, fix issues in code you touch, avoid tacking new features onto muddy interfaces, fail loudly instead of silently swallowing errors, keep code strictly typed, preserve clear state boundaries, remove duplicate or dead logic, break up overly long functions, avoid hidden import-time side effects, respect module boundaries, and favor correctness-by-construction over relying on callers to use an API correctly.

## CRITICAL — Secrets Never Leave the Server

API keys, tokens, and credentials must never appear in API responses, logs, traces, exception messages, or URLs. Any violation is a blocking finding regardless of severity elsewhere in the PR.

- Response models expose credentials only as `*_set: bool` and `*_hint` (redacted) fields — never the raw value. A raw credential field in any read endpoint's response model is a blocking finding.
- Show-once is the only exception: an endpoint that creates or rotates a credential may return it exactly once in that response; every subsequent read returns only set/hint.
- Redaction must not reconstruct the secret: a first4…last4 hint is acceptable only for values long enough that it reveals a small fraction (≥16 chars); shorter values get fixed-width masking that reveals neither content nor length.
- No credential values in `log.*` calls at any level, in tracing spans, in error/exception text returned to clients, or interpolated into URLs.
- Flag any new code path that serializes a stored credential (`model_dump`, `JSONResponse`, f-string, `repr` in logs) outside the provider/SDK call that consumes it.
- Frontend: credential inputs are write-only; never store a fetched raw credential in component state except the show-once response, and never render one outside that flow.

## TODOs

Whenever a TODO is added, there must be an associated name or ticket in the form `TODO(name): ...` or `TODO(1234): ...`.

## Debugging Code

Remove temporary debugging code before merging — stray `print()`, `console.log()`, dump-to-file, or scratch endpoints have no place on `main`.

## Hardcoded Booleans

When hardcoding a boolean variable to a constant value, remove the variable entirely and clean up all places where it's used rather than just setting it to a constant. Dead branches are noise.

## Architectural Seams — Honor the Boundaries

Interfaces that must NOT be bypassed:

- LLM calls only through the central LLM client; no direct provider SDK imports outside the matching provider module.
- Auth via the dependency-injected `require_user` / `current_user` helpers; no raw session reads in routers.
- Wiki ACL via the ACL module + `require_can`; never read/write ACL tables directly.
- DB: SQLAlchemy 2.0 ORM, repos return dicts. Pydantic, NOT `@dataclass`, for structured records.
- Wiki commits via the git wrapper; never shell out to `git` elsewhere.
- Background work via the `pgmq`-backed task queues; no ad-hoc threading.
- Logging via the centralized `setup_logging`; no `print()`, no `logging.basicConfig`.
- Tracing via the tracing module; never import provider SDKs outside that package.

Flag any new code that bypasses these seams.

## No Raw SQL Outside the Allowed Sites

Raw SQL is permitted only in narrowly scoped DB-extension wrappers (FTS, task queue). Anywhere else, use the ORM session — `session.execute(text(...))` in business code is a regression to flag.

## Frontend — Design Tokens + Components

- No raw hex colors, radii, or shadows in React components — pull from the centralized theme module. If a shade isn't there, add it there first.
- No `background: "white"` (or any literal); use `color.bg.page`.
- One `Button` component per app surface; prefer the OPAL primitives (`Button`, `Tag`) for new components. Don't roll a new bespoke `<button style={{...}}>` for primary/secondary/danger chrome.
- Modal scrims use `color.overlay`; modal shadow uses `shadow.modal`. Don't introduce slate-tinted or pure-black scrims.

## Network — Only via `apiFetch`

The shared `apiFetch` helper is the only allowed seam for talking to the backend from the frontend. Raw `fetch(...)` to internal endpoints loses the `credentials: "include"` + JSON parsing + `ApiError` envelope.

## Auth — Only via the Provider

Pages call `useRequireAuth()` to gate and `useAuth()` to read state. Don't call `/api/auth/me` from a component — the provider owns it.

## Comments — Describe Current Behavior

Code comments describe what the code IS, not what it replaced or used to be. No dates, no PR numbers, no audit-reference tokens (`AF#X`, `R2#7`, `P1 #3`). Migration / refactor context belongs in commit messages, not source comments.

## Pre-Commit Discipline

The repo's pre-commit hooks (ruff + basedpyright strict, plus frontend typecheck in CI) are the authority. Code that lints clean locally but trips them on push is a regression. Run `pre-commit run --files <changed>` before requesting review.
