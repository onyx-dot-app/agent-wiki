# Auth and Permissions

How callers authenticate, how the active user is resolved, and how
per-page permissions get enforced. This doc covers the whole stack —
the seams in the backend, the API surface, and the bits of the
frontend that show up to users.

For the request/response shapes themselves, see `docs/api.md`. For
why permissions live in Postgres (and don't ride along with the wiki
git repo or the `wiki-data` volume), see the operational warning in
`Wiki Project/Running Locally.md` §"Permissions don't travel".

## The picture

```
                                ┌────────────────────────────┐
                                │        Browser / UI        │
                                └──────┬───────────────┬─────┘
                          session      │               │  apiFetch
                          cookie       │               │
                                       │               │
                                       │   ┌───────────▼────────────┐
                                       │   │ frontend/src/lib/      │
                                       │   │   auth.tsx (useAuth,   │
                                       │   │     useRequireAuth)    │
                                       │   │   permissions.ts (SWR) │
                                       │   └───────────┬────────────┘
                                       │               │
┌──────────────┐                       │               │
│ MCP client   │ Bearer mcp_<token>    │               │
│ (Claude Code,│  ─────────────────┐   │               │
│  Cursor, …)  │                   │   │               │
└──────────────┘                   ▼   ▼               ▼
                             ┌──────────────────────────────────┐
                             │          FastAPI backend         │
                             │                                  │
                             │  app/auth/deps.py                │
                             │   require_user / require_admin / │
                             │   require_bearer + CurrentUser-  │
                             │   Middleware                     │
                             │                                  │
                             │  app/auth/__init__.py            │
                             │   current_user_ctx ContextVar /  │
                             │   set_current_user / require_can │
                             │                                  │
                             │  app/api/auth.py    (login/me)   │
                             │  app/api/admin.py   (users)      │
                             │  app/api/permissions.py          │
                             │   (groups + ACL + transfer)      │
                             │  app/api/mcp_tokens.py           │
                             └──────┬───────────────────────────┘
                                    │
                                    ▼
                       ┌──────────────────────────┐
                       │   app/wiki/acl.py        │
                       │   ─ owner repo           │
                       │   ─ ACL grant repo       │
                       │   ─ effective() resolver │
                       │   ─ visible_paths_filter │
                       │   ─ lifecycle hooks      │
                       └──────┬───────────────────┘
                              │
                              ▼
                  ┌─────────────────────────────┐
                  │   Postgres (ORM tables)     │
                  │   ─ users                   │
                  │   ─ mcp_tokens              │
                  │   ─ groups, group_members   │
                  │   ─ wiki_owners             │
                  │   ─ acl_entries             │
                  └─────────────────────────────┘
```

## Authentication

Two ways in for humans, one way in for agents. All three converge on
the same `current_user_ctx` ContextVar (read via
`app.auth.current_user()`) so everything below this layer is
auth-mode-agnostic.

### Modes

`AUTH_MODE` (env, default `basic`) picks the human flow:

- **`basic`** — email + password. Passwords are bcrypt-hashed via
  `app/auth/passwords.py`. Login/signup/logout in
  `app/api/auth.py`, verification in `app/auth/basic.py:authenticate`.
- **`oidc`** — authorization-code flow via authlib. Wired in
  `app/auth/oidc.py:init_oauth` (registered when issuer + client id +
  secret are all set). Login at `GET /api/auth/oidc/login`, callback
  at `GET /api/auth/oidc/callback`. The callback verifies
  `email_verified`, runs the email through the whitelist, then
  `upsert_oidc_user` creates the row (with a random unusable password
  hash, since the schema demands non-null) or returns the existing one.

Both modes hand off to `_start_session(user)` which writes
`request.session["user_id"] = user.id` (Starlette's
`SessionMiddleware`, installed in `app/main.py:create_app`, signs and
serializes the cookie). From that point the session cookie alone
authenticates each request — `CurrentUserMiddleware` reads
`request.session["user_id"]`, loads the `User`, and binds it into
`current_user_ctx` for the request's lifetime.

### Signup whitelist

`ALLOWED_EMAILS` in env (comma-separated). Empty/unset means signup is
open. Exact addresses or domain wildcards (`*@onyx.app`). Used by both
`/api/auth/signup` and the OIDC callback. Code in
`app/auth/whitelist.py`.

`GET /api/auth/config` returns `{mode, signup_open}` so the frontend
knows whether to render the signup form.

### First-user-is-admin

`users_repo.create` (`app/auth/users.py:51`) checks
`existing_count == 0` and stamps `is_admin=True` on the very first
user — no special bootstrap step. Same convention applies to OIDC
sign-ins (`upsert_oidc_user`).

### Last-admin guard

You cannot demote or delete the only remaining admin.
`app/api/admin.py` calls `users_repo.admin_count() <= 1` before either
mutation and returns a 400 if it would drop to zero.

### MCP bearer tokens

External agents (Claude Code, Cursor, custom harnesses) authenticate
per-request with `Authorization: Bearer mcp_<token>`.

- Tokens are minted via `app/auth/mcp_tokens.py:create` — prefix
  `mcp_` + 24 random bytes (`secrets.token_urlsafe`). The raw value is
  shown to the user **once**; the DB stores a bcrypt hash.
- `app/auth/deps.py:require_bearer` is a FastAPI dependency that
  verifies the `Authorization: Bearer mcp_<token>` header, resolves
  it through `tokens_repo.verify` (linear bcrypt walk — fine at
  hand-minted scale), and returns the resolved `User`. The MCP route
  wraps its dispatch in `with set_current_user(user):` so downstream
  helpers (`current_user`, `require_can`, ACL hooks, agent-activity
  attribution, trigger `actor`) see the right principal.
- This is why `current_user()` is ContextVar-only: both
  cookie-authenticated requests (via `CurrentUserMiddleware`) and
  bearer-authenticated requests (via the explicit
  `set_current_user(...)` block) populate the same
  `current_user_ctx`, so the read path is identical.

### Why `current_user()` returns `None` outside a request

Background tasks and tests run with no HTTP request, so
`CurrentUserMiddleware` never fires and `current_user_ctx` keeps its
`None` default. `app/auth/__init__.py:current_user` just reads the
ContextVar, so the anonymous principal is the natural fallback. The
ACL resolver applies the same rules (`everyone` grants only) it
would for a logged-out caller, and the chat agent / MCP job worker
explicitly bind the user via `with set_current_user(load_user(uid)):`
before doing any wiki write. See
`Wiki Project/Specific Features/MCP Server Inbound.md` for the
worker handoff.

## Authorization seams

FastAPI dependencies + a helper gate everything. **Use these — don't
read `request.session` or `acl_entries` directly from a router.**

| Helper | Purpose |
|---|---|
| `Depends(require_user)` | 401 if no user. The default on every route except the explicit public endpoints (signup, login, `/auth/config`, webhooks). Returns the typed `User`. |
| `Depends(require_admin)` | 401 if no user, 403 if `is_admin` is false. Returns the `User`. |
| `Depends(require_bearer)` | MCP-only. Verifies the `Authorization: Bearer mcp_<token>` header. Returns the `User`; the caller wraps work in `with set_current_user(user):` so downstream code sees the principal. |
| `require_can(action, path, user)` | Raises `PermissionDenied` (→ 403) if `user` lacks `read` or `write` on the wiki path. Admins always pass. Called after `Depends(require_user)` so the resolved `User` is on hand. |

`require_can` lazily imports `app.wiki.acl` to avoid a cycle, then
calls `acl.can(user_id, is_admin, action, path)`.

## The ACL model

Two tables, both keyed by canonicalized wiki paths
(`app.wiki.filesystem.safe_rel_path`, no leading slash, root = `""`).

### `wiki_owners`

One row per page. The owner has unconditional read / write / share /
transfer / delete on that page. Used as the fast-path in `effective`
(no ACL row lookup needed if `user_id == owner`). Code in
`app/wiki/acl.py`: `get_owner`, `set_owner`, `transfer_owner`.

### `acl_entries`

Page-level and folder-level grants. Composite of:

- `resource_kind` — `"page"` or `"folder"`
- `resource_path` — canonicalized path; root is `""`
- `principal_kind` — `"user"`, `"group"`, or `"everyone"`
- `principal_id` — user/group id, or empty for `everyone`
- `permission` — `"read"` or `"write"` (write implies read)
- `granted_by_user_id` — audit

Folder grants cascade to all descendant pages. Grants are **additive**
— there's no explicit deny in v1.

Functions in `app/wiki/acl.py`:

- `grant(...)` — idempotent insert, validates the enums, canonicalizes
  paths.
- `revoke(entry_id)` — delete by id.
- `list_for_path(path)` — page rows first, then folder rows ordered
  deepest-ancestor first (so the share UI can render "deepest match
  wins" displays).
- `delete_all_for_path(path)` — drop owner + page-level ACL on page
  delete.

## The resolver

`effective(user_id, is_admin, path) → set[str]` (returns a subset of
`{"read", "write"}`). Mirrors the design doc step-for-step:

1. `is_admin` → `{"read", "write"}` (admin override).
2. `user_id == owner(path)` → `{"read", "write"}`.
3. Walk page-level ACL rows at `path` plus folder ACL rows at every
   ancestor (including root `""`). Match a row when:
   - `principal_kind == "everyone"`, **or**
   - `kind == "user"` and `principal_id == user_id`, **or**
   - `kind == "group"` and the user is a member (groups expanded via
     `groups_repo.group_ids_for_user`).
4. Union the matched permissions; `write` implies `read`.

### Implicit-public fallback

If a path has **no owner row and zero ACL rows** at the page or any
folder ancestor, `effective` returns `{"read", "write"}`. This covers
test setups and seed scripts that bypass the lifecycle hook by
calling `git.commit_file` directly. The first owner stamp or ACL grant
"manages" the path and the implicit-public falls away. Same logic in
the bulk filter.

`can(user_id, is_admin, action, path)` is the boolean wrapper.

## Bulk visibility filter

For listing and search we can't run the resolver per row. Two helpers
in `app/wiki/acl.py`:

- `visible_paths_filter(user_id, is_admin, path_column)` — a
  SQLAlchemy predicate. Mirror of `effective` rewritten as `EXISTS`
  subqueries; admins get `literal(True)`; folder cascade is
  `path_column.like(AclEntry.resource_path + '/%')`. Plug into
  `documents` and search `WHERE` clauses.
- `filter_paths_in_python(user_id, is_admin, paths)` — Python-side
  fallback for in-memory path sets (e.g. directory listings already
  built from `git ls-files`).

## Lifecycle hooks

Every wiki write should flow through `app/wiki/notify.py` so ACL
state stays consistent with the git working tree.

- `after_doc_write(rel_path, sha, change_kind, actor, owner_user_id)`
  — on `change_kind="create"` calls `acl.on_page_created(path,
  owner_user_id)`, which stamps the owner *and* seeds default-public
  rows (`everyone` read + write). Idempotent — safe to call again.
  Then enqueues reindex + trigger fan-out and publishes the MCP
  resource update.
- `after_doc_delete(rel_path, sha, actor)` — calls
  `acl.on_page_deleted` to drop owner + page-level ACL, then the
  resource delete fan-out.
- `acl.on_path_moved(moves)` rewrites `resource_path` in both
  `wiki_owners` and `acl_entries` for page + folder moves.

Don't call into `wiki_owners` / `acl_entries` from routers or agent
tools — go through the public functions in `app.wiki.acl`.

## API surface

Routers + their mount points:

- `/api/auth` (`app/api/auth.py`) — `signup`, `login`, `logout`,
  `me`, `oidc/login`, `oidc/callback`, `config`.
- `/api/admin` (`app/api/admin.py`) — admin-only user management
  with the last-admin guard.
- `/api/users` (`app/api/users.py`) — broader user listing for the
  share dialog's principal picker.
- `/api/mcp/tokens` (`app/api/mcp_tokens.py`) — personal-token CRUD
  (the raw token returns once on create).
- `/api` (`app/api/permissions.py`) — groups + ACL:
  - `GET/POST /api/groups`, `GET/DELETE /api/groups/<id>`,
    `POST/DELETE /api/groups/<id>/members[/<user_id>]`. Listing is
    `Depends(require_user)` (admins see all, users see their own);
    mutations use `Depends(require_admin)`.
  - `GET /api/wiki/acl?path=<path>` — page grants. Owner-or-admin
    only (gate is `_can_manage_path`).
  - `POST /api/wiki/acl` — create grant. Owner-or-admin.
  - `DELETE /api/wiki/acl/<entry_id>` — revoke. Owner-or-admin
    (entry is fetched first to determine the path).
  - `POST /api/wiki/transfer-ownership` — transfer page ownership.
    Owner-or-admin.

Errors come back as `{"error": "<message>"}` with the right status —
the frontend's `ApiError` parses that envelope.

## Frontend

### Auth context

`frontend/src/lib/auth.tsx`:

- `AuthProvider` fetches `/auth/me` and `/auth/config` on mount.
- `useAuth()` returns `{ user, config, login, signup, logout, … }`.
  Throws if used outside the provider.
- `useRequireAuth()` redirects to `/login?next=<pathname>` when the
  user is null. Top-level pages call this; page bodies don't ping
  `/auth/me` themselves.
- All network calls go through `apiFetch` in `src/lib/api.ts` with
  `credentials: "include"` so the session cookie travels.

### Permissions hooks + UI

`frontend/src/lib/permissions.ts`:

- `useGroups()`, `useGroup(id)`, `usePageAcl(path)` — SWR-backed reads.
- `createGroup`, `addGroupMember`, `removeGroupMember`, `grantAcl`,
  `revokeAcl`, `transferOwnership` — mutations.
- `visibility(acl)` returns `"public-write" | "public-read" |
  "private"` based on `everyone` grants at page + folder level;
  `isPrivate(acl)` is the boolean wrapper used by chrome to decorate
  navigation.

UI surfaces:

- `frontend/src/app/admin/users/page.tsx` — list/promote/demote/delete
  users; mirrors the backend's last-admin and self-delete guards in
  the buttons.
- `frontend/src/app/admin/groups/page.tsx` — group CRUD + member
  management.
- `frontend/src/components/wiki/ShareDialog.tsx` — per-page share
  modal. Loads `usePageAcl(path)` and `useGroups()`, fetches a user
  list for the principal picker, calls `grantAcl` / `revokeAcl` /
  `transferOwnership`. Renders the current visibility badge on top.

## What lives where — quick reference

| Concern | File |
|---|---|
| `current_user_ctx` ContextVar, `set_current_user`, `require_can` | `backend/app/auth/__init__.py` |
| FastAPI deps (`require_user`, `require_admin`, `require_bearer`) + `CurrentUserMiddleware` | `backend/app/auth/deps.py` |
| Email/password auth | `backend/app/auth/basic.py`, `passwords.py` |
| User repo | `backend/app/auth/users.py` |
| Group repo | `backend/app/auth/groups.py` |
| Signup whitelist | `backend/app/auth/whitelist.py` |
| OIDC | `backend/app/auth/oidc.py` |
| MCP token repo | `backend/app/auth/mcp_tokens.py` |
| MCP bearer dependency | `backend/app/auth/deps.py:require_bearer` |
| Wiki ACL repo + resolver + filter | `backend/app/wiki/acl.py` |
| ACL lifecycle hooks | `backend/app/wiki/notify.py` |
| ORM tables (`users`, `mcp_tokens`, `groups`, `group_members`, `wiki_owners`, `acl_entries`) | `backend/app/db/models.py` |
| Auth API | `backend/app/api/auth.py` |
| Admin API | `backend/app/api/admin.py` |
| Permissions API (groups + ACL) | `backend/app/api/permissions.py` |
| MCP-token API | `backend/app/api/mcp_tokens.py` |
| Frontend auth context | `frontend/src/lib/auth.tsx` |
| Frontend permissions hooks | `frontend/src/lib/permissions.ts` |
| Per-page share UI | `frontend/src/components/wiki/ShareDialog.tsx` |
| Admin user/group pages | `frontend/src/app/admin/{users,groups}/page.tsx` |

## Open follow-ups

- **Deny rows.** Grants are additive in v1; there's no way to grant
  "everyone read" then carve out a specific user. Tracked alongside
  the broader permissions design.
- **`.acl.yaml` in git.** Permissions live in Postgres only — they
  don't travel with `git clone` or a `wiki-data` snapshot. A
  git-backed mirror is on the backlog; for now use `pg_dump` of the
  permission tables (recipe in `Wiki Project/Running Locally.md`).
- **Group self-service.** Mutations are admin-only. Users can be
  members but can't create groups themselves yet.
- **Token scopes.** MCP bearer tokens currently inherit the issuing
  user's full permission set. Per-token scoping (e.g. read-only
  tokens, single-doc tokens) is an obvious next step but not in v1.
