# Permissions

> **Status: implemented (v1).** This doc owns the permissioning model
> for wiki pages and triggers: users, groups, RBAC shape, ACL
> resolution, search/query integration, and the migration path.
> Implementation lives in `app/db/models.py` (schema), `app/wiki/acl.py`
> (resolver + lifecycle hooks), `app/auth/groups.py` (group repo),
> `app/auth/__init__.py:require_can` (route gate), `app/api/permissions.py`
> (HTTP surface), and the frontend pieces under
> `frontend/src/lib/permissions.ts`,
> `frontend/src/components/wiki/ShareDialog.tsx`, and
> `frontend/src/app/admin/groups/page.tsx`. Update this doc when
> reality diverges. Cross-area context lives in
> [`../architecture_and_progress.md`](../architecture_and_progress.md);
> trigger ownership history is in
> [`../natural-language-triggers/natural-language-triggers.md`](../natural-language-triggers/natural-language-triggers.md).

_Last updated: 2026-05-09_

---

## TL;DR

- **Wiki pages** have an **owner** (the creator) plus an ACL of additional
  user/group grants for `read` or `write`. Folders carry ACLs too, and
  grants on a folder cascade to every descendant.
- **New pages default to public** (everyone read + write). The owner can
  flip a page (or folder) to **private** at any time, then share with
  specific users/groups.
- **Triggers** are **owner-only, no sharing in v1.** A trigger is visible
  and editable only by its creator. (Existing behaviour — see
  [`../natural-language-triggers/natural-language-triggers.md`](../natural-language-triggers/natural-language-triggers.md).)
- **Storage:** ACLs live in **Postgres only**. The git repo and the
  `wiki-data` volume contain document content; **permissions do not
  travel with a clone, push, or volume export.** See
  [Export warning](#export-warning-permissions-do-not-travel-with-the-repo).
- **Enforcement:** the Flask app is the only enforcement boundary.
  Filesystem permissions on disk are not used (rationale below).

---

## Why not Unix filesystem permissions?

The wiki working tree lives on disk, so reaching for `chmod`/`chown`
feels natural. It is not the right mechanism for this app:

| Concern | Why filesystem perms fail here |
|---|---|
| **App users ≠ OS users** | Sign-up creates a row in `users`; it does not create a Unix user. Web, worker, and git processes all run as one OS user inside Docker. |
| **Single enforcement boundary** | Access is mediated by Flask routes, not by direct file reads. `stat()` cannot stop a Flask handler that already chose to open the file. |
| **Git doesn't carry modes** | Beyond the executable bit, POSIX modes are not committed. A clone or rebuild loses them. |
| **Search filtering** | BM25 search (`pg_textsearch`) needs to filter results by visibility in SQL. The filesystem cannot answer "which paths can user X read?" in one query. |
| **Groups** | OS groups are static and not the same set as our app-level groups with membership tables. |
| **Triggers as files** | `.trigger_*.yaml` files sit inside the wiki tree. Folder-level Unix perms would conflict with "triggers are private to creator regardless of where they live." |
| **POSIX ACLs** | Platform-dependent, not in git, and still don't solve the "app-user ≠ OS-user" gap. |

We **borrow the Unix mental model** (owner / group / other × read /
write, plus folder inheritance) but implement it as Postgres tables that
the app consults on every read, write, and search.

---

## Resource model

Three principal kinds, three resource kinds:

**Principals**

- `user` — a row in `users`.
- `group` — a row in `groups`, expanded via `group_members`.
- `everyone` — synthetic principal matching every authenticated user.
  Used so "share with the whole company" doesn't require maintaining a
  group with all members.

**Resources**

- `page` — a wiki document (path = `Document.path`, e.g. `/specs/auth.md`).
- `folder` — a path prefix (e.g. `/specs/`). Folders are virtual: they
  exist whenever an ACL row is created for them; there is no
  `wiki_folders` table.
- `trigger` — a row in `triggers`. **Not in the ACL system in v1** —
  enforced by `triggers.owner_user_id` only.

**Permissions**

- `read` — view content, appear in search, render in the wiki UI.
- `write` — `read` + edit + commit. Owners additionally have **manage**
  (share, transfer, delete) — manage is implicit in ownership and not a
  separate grant in v1.

`write` does **not** auto-imply `read` in storage; if a principal has
`write`, the resolver treats them as also having `read`. This keeps the
table simple (no compound enum) while preserving the conventional
semantics.

---

## Implementation deviations from the original proposal

- **No `documents.owner_user_id`.** The `documents` ORM model was unused
  in v0 (no inserts), so adding an owner column there would have been a
  no-op. Ownership lives in a small `wiki_owners(path, owner_user_id)`
  table keyed by canonicalized path instead. If `documents` ever grows
  rows, consolidate later.
- **No data backfill in the Alembic migration.** Bootstrap is at app
  startup (`app.wiki.acl.bootstrap_acls_if_empty`), called from
  `main.create_app` after `ensure_wiki_repo`. It walks
  `git ls-files`, seeds `(everyone, read)` + `(everyone, write)` for
  every tracked `.md` page that doesn't already have an ACL row.
- **Implicit-public fallback.** A page with **no owner row AND no ACL
  rows anywhere on its path** is treated as having `everyone read+write`.
  This covers (a) pages predating the feature, and (b) test setups
  that bypass `notify.after_doc_write` by calling `git.commit_file`
  directly. The first owner assignment or ACL grant marks the path as
  "managed" and the implicit-public falls away.
- **Migration is `IF NOT EXISTS`-shaped.** `0001_initial` materializes
  `Base.metadata.create_all(bind)`, so on fresh databases authored
  after this revision shipped the four new tables already exist before
  `0002_permissions` runs. The `0002` migration uses
  `inspector.has_table(...)` guards so it's a no-op in that case and a
  real ALTER for production databases that ran `0001` before this
  revision existed.

## Schema

```python
class Group(Base):
    __tablename__ = "groups"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[str] = mapped_column(Text, server_default=_NOW_TEXT_DEFAULT)


class GroupMember(Base):
    __tablename__ = "group_members"
    group_id: Mapped[str] = mapped_column(
        Text, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    added_at: Mapped[str] = mapped_column(Text, server_default=_NOW_TEXT_DEFAULT)


class AclEntry(Base):
    __tablename__ = "acl_entries"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    resource_kind: Mapped[str] = mapped_column(Text, nullable=False)   # 'page' | 'folder'
    resource_path: Mapped[str] = mapped_column(Text, nullable=False)   # canonical, no trailing slash
    principal_kind: Mapped[str] = mapped_column(Text, nullable=False)  # 'user' | 'group' | 'everyone'
    principal_id: Mapped[str | None] = mapped_column(Text)             # NULL for 'everyone'
    permission: Mapped[str] = mapped_column(Text, nullable=False)      # 'read' | 'write'
    granted_by_user_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[str] = mapped_column(Text, server_default=_NOW_TEXT_DEFAULT)
    __table_args__ = (
        UniqueConstraint("resource_kind", "resource_path", "principal_kind",
                         "principal_id", "permission"),
        Index("idx_acl_resource", "resource_kind", "resource_path"),
        Index("idx_acl_principal", "principal_kind", "principal_id"),
    )
```

Plus a tiny ownership table (because `documents` has no inserts in v0):

```python
class WikiOwner(Base):
    __tablename__ = "wiki_owners"
    path: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_user_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("users.id", ondelete="SET NULL")
    )
```

`SET NULL` on owner deletion (rather than `CASCADE`) so the page
survives an account removal; admin can re-assign via the transfer
endpoint.

Repo modules:

- `app/auth/groups.py` — CRUD + membership.
- `app/wiki/acl.py` — owner repo + grant/revoke + resolver + lifecycle hooks.

---

## Effective-permission resolution

Given a user `U` and a page path `P`, can `U` perform action `A` ∈
{read, write}?

```
1. If U.is_admin → allow (admin override; v1).
2. If U is owner(P) → allow.
3. Collect grants:
   a. Page-level: rows where (resource_kind='page', resource_path=P).
   b. Folder-level: walk parents of P from deepest to root '/'.
      For each ancestor F, rows where (resource_kind='folder',
      resource_path=F).
4. For each grant row, principal matches when:
      principal_kind='everyone'                       → match
   OR principal_kind='user' AND principal_id=U.id     → match
   OR principal_kind='group' AND U ∈ members(group)   → match
5. Of the matching rows, take the union of permissions. Treat 'write'
   as also granting 'read'.
6. Allow iff A ∈ effective_permissions.
```

**Additive only — no deny rows in v1.** A folder grant cannot be
"taken back" by a child. To lock down a subtree, restrict each page
explicitly (or move the page out of the public folder). Deny semantics
are listed in [Open questions](#open-questions).

**Caching.** Per-request memoise group memberships for `U`. Per-search
or bulk-listing operations should compute "what paths can U read?" via a
single SQL query that joins `documents` against `acl_entries`, rather
than calling the resolver in a loop.

---

## Default visibility — the `everyone` grant

New page creation seeds two ACL rows:

- `(page, P, everyone, NULL, read)`
- `(page, P, everyone, NULL, write)`

The page is now public. The owner can revoke either or both via the
share UI; doing so makes the page "private" (only owner + explicit
grants). There is no `visibility` enum on the document — public state is
just the presence of `everyone` rows.

The same pattern applies to folder-level grants when a user creates a
folder share. There is no automatic root-`/` grant — defaults are
attached at the resource that exists (the page).

---

## Search & listing

Two query patterns, both pushed into SQL.

**Listing (the wiki tree, the docs tab):**

```sql
SELECT d.* FROM documents d
WHERE
  d.owner_user_id = :uid
  OR EXISTS (
    SELECT 1 FROM acl_entries a
    WHERE a.resource_kind = 'page' AND a.resource_path = d.path
      AND (
        a.principal_kind = 'everyone'
        OR (a.principal_kind = 'user' AND a.principal_id = :uid)
        OR (a.principal_kind = 'group'
            AND a.principal_id IN (SELECT group_id FROM group_members WHERE user_id = :uid))
      )
  )
  OR EXISTS (
    SELECT 1 FROM acl_entries a
    WHERE a.resource_kind = 'folder'
      AND d.path LIKE a.resource_path || '/%'
      AND (... same principal predicate ...)
  );
```

**Search (BM25):** the same predicate is `JOIN`ed onto the
`pg_textsearch` query in `app/db/fts.py` so unauthorised hits are
filtered before scoring is paid for.

If this becomes hot, materialise a `user_visible_paths(user_id,
resource_kind, resource_path)` table refreshed by a `wiki_bm25_queue`
task on ACL change. Don't pre-optimise — measure first.

---

## Triggers

**Triggers are not in the ACL system in v1.** A trigger:

- Is created with `owner_user_id = current_user.id` (already enforced).
- Is visible / editable / deletable only by its owner.
- Fires regardless of who edited the underlying doc, but **runs as its
  owner** — when the trigger reads or writes wiki content during
  evaluation, those reads/writes are subject to the owner's permissions
  on the relevant pages.
- If the owner loses read access to its `scope_path`, the trigger
  becomes a no-op until access is restored (or the trigger is deleted).
  We do **not** auto-delete; the row stays for audit.
- The trigger's YAML file (`.trigger_<id>_*.yaml`) lives inside the
  wiki tree on disk for storage purposes but is **not surfaced as a
  wiki page** in the UI or in search. ACLs do not apply to it.

Wiki ACLs apply at three boundaries on the trigger surface
(implemented 2026-05-09 — see
[`../natural-language-triggers/natural-language-triggers.md`](../natural-language-triggers/natural-language-triggers.md)
"Wiki ACLs apply at every boundary"):

1. **Create-time** — `app/api/triggers.py` and the `create_trigger` /
   `update_trigger` agent tools require `read` access on `scope_path`
   (and re-check on update so revoked access blocks further mutation).
2. **Fire-time** — `app/tasks/triggers.py:fan_out_trigger_eval`
   re-checks `acl.can(owner, "read", doc_path)` per trigger before
   evaluating; failed checks `continue` without recording a fire row.
3. **Events list** — `/api/events` is owner-scoped by joining through
   `triggers.owner_user_id`; cross-owner detail reads return `404`.

Sharing triggers (group ownership, "see team triggers") remains
**backlog** — see the trigger doc for context. When we lift the
restriction, the resolver above already accommodates trigger ACL rows
without a schema change.

---

## Admin role

`User.is_admin` already exists. In v1, admins **bypass** all page-level
permission checks: they can read and write any page. This matches how
admin works elsewhere in the app and gives us a recovery path when a
sole owner is offline. Admin actions on others' pages should emit an
`events` row for audit.

Admins can also manage groups (create, delete, add/remove members).
Whether non-admins can create groups is an [open
question](#open-questions); v1 default is **admin-only group
management**.

---

## API surface (sketch)

New blueprint `app/api/permissions.py`:

- `GET  /api/groups` — list groups (admin sees all; user sees groups
  they're in).
- `POST /api/groups` — create (admin).
- `POST /api/groups/<id>/members` — add member (admin).
- `DELETE /api/groups/<id>/members/<user_id>` — remove member (admin).

New endpoints on the existing wiki blueprint:

- `GET    /api/wiki/acl?path=<path>` — list grants for a page or
  folder. Owner or admin only.
- `POST   /api/wiki/acl` — add a grant (owner / admin).
- `DELETE /api/wiki/acl/<id>` — revoke a grant.
- `POST   /api/wiki/transfer-ownership` — change `owner_user_id`
  (current owner or admin).

Every wiki read/write route gains a `require_can(action, path)` check
that calls `app/wiki/acl.py:effective(user, path)`. Failures return
`403 {"error": "forbidden"}`.

---

## Migration

**Existing content** (live before this lands) is seeded with full
public access:

1. Add `owner_user_id` to `documents`. Backfill: for each row, set to
   `NULL` (no historical owner). Admin can claim later via the transfer
   endpoint.
2. For every existing page `P`, insert
   `(page, P, everyone, NULL, read)` and
   `(page, P, everyone, NULL, write)` ACL rows.
3. Going forward, document-create paths seed the same two rows
   automatically.

**Existing users**: no change. Group membership is opt-in; an empty
`group_members` set is fine.

**Existing triggers**: no change — `triggers.owner_user_id` already
exists and is already enforced.

The migration is one Alembic revision (`alembic revision
--autogenerate -m "permissions"`), but the autogenerate **will not**
produce the data-backfill INSERTs. Add those by hand in the same
revision file (`op.execute(...)`) so `init_db()` on boot applies
schema + backfill atomically.

---

## Export warning — permissions do not travel with the repo

> **Operational warning.** ACLs, group definitions, and group
> memberships live in **Postgres only**. They are **not** committed
> to the wiki git repo, **not** stored in the `wiki-data` Docker
> volume, and **not** exported by any of:
>
> - `git clone`, `git push`, or any other git operation against the
>   wiki repo.
> - Copying the `wiki-data` volume to a new host.
> - A filesystem-level backup of the working tree.
>
> A wiki cloned or restored from the volume **alone** comes back as if
> every page had no permissions configured. To round-trip permissions,
> you must back up Postgres (`pg_dump` of the `groups`,
> `group_members`, `acl_entries` tables and `documents.owner_user_id`)
> alongside the repo. Document this in the runbook before recommending
> any export workflow to a customer.
>
> This is an explicit design choice — we picked Postgres-only storage
> for query speed and search-filter integration, and accepted that
> exports require a separate Postgres dump. The alternative (mirroring
> ACLs into `.acl.yaml` files in git) is recorded in
> [Open questions](#open-questions) as a v2 candidate.

---

## What this doesn't do (out of scope for v1)

- **Per-field permissions** within a page (e.g., redact a section).
- **Time-bound grants** (expiring shares).
- **Explicit deny rows.** All grants are additive.
- **Sharing for triggers.** Owner-only.
- **Inviting users by email** to a page — they must already have an
  account.
- **Audit UI.** ACL changes hit `events` but there's no dedicated view.
- **Permission templates / inherited roles** beyond folder cascade.

---

## Open questions

- **Sealed pages / explicit deny.** Without deny, "this folder is
  public except for one secret page" requires either moving the secret
  page out, or a per-page revoke. Worth adding `permission='deny'`
  rows in v2? Most-specific-wins resolution gets us most of the way.
- **Mirror ACLs into `.acl.yaml` git files** so permissions survive a
  clone/restore. Same pattern triggers use today. Cost: two sources of
  truth and a reconciler.
- **Group self-service.** Should non-admins be able to create groups
  and invite members, or stay admin-managed? Defaulting to admin-only
  for v1.
- **Page move/rename.** ACL rows are keyed by path. On rename,
  `acl_entries.resource_path` must be updated in the same transaction
  as the git commit + `documents.path` update. Same applies to folder
  moves (rewrite all descendant ACL paths).
- **Bulk operations.** "Make this whole folder private" today means
  deleting `everyone` rows from every descendant page. UI affordance
  TBD.
- **Folder ownership.** Folders have ACLs but no owner. Do we need a
  folder-meta table for ownership/auditing, or is "the folder belongs
  to whoever owns the pages in it" good enough? v1 says good enough.
