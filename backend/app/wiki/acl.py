"""Wiki page permissions — owner + ACL repo + resolver.

Source of truth for everything described in
``local_data/wiki/permissions/permissions.md``. Three responsibilities,
all keyed by canonicalized wiki paths (``app.wiki.filesystem.safe_rel_path``,
no leading slash; root = ``""``):

1. **Ownership** — the ``wiki_owners`` table. The owner has unconditional
   read/write/share/transfer/delete on a page; admins bypass all checks.
2. **ACL grants** — the ``acl_entries`` table. Page-level and folder-level
   grants of ``read`` / ``write`` to a user, group, or the synthetic
   ``everyone`` principal. Folder grants cascade to descendants. Grants
   are additive — no explicit deny in v1.
3. **Resolver** — :func:`effective` answers "what can user U do at path
   P?", and :func:`visible_paths_subquery` returns a SQL fragment so
   list/search queries can filter in one round trip rather than calling
   the resolver in a loop.

Lifecycle hooks (:func:`on_page_created`, :func:`on_page_deleted`,
:func:`on_path_moved`) are called from ``app.wiki.notify`` so every
write site flows through one seam.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Iterable

from sqlalchemy import (
    ColumnElement,
    Select,
    and_,
    delete,
    exists,
    func,
    literal,
    or_,
    select,
    update,
)

from app.auth import groups as groups_repo
from app.db.models import AclEntry, WikiOwner
from app.db.session import session
from app.models.wiki import PageKind, PathMove
from app.wiki.filesystem import common_folder_rename

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Path helpers                                                                #
# --------------------------------------------------------------------------- #


def _ancestors(path: str) -> list[str]:
    """All folder ancestors of ``path``, deepest first, root last.

    ``path`` is treated as a page path; the page itself is not included.
    The root is represented as ``""`` (matching ``parent_dirs`` in
    ``app.wiki.filesystem``). Mirrors the cascade walk in the design
    doc: deepest folder grant wins for "most specific match" displays,
    but the resolver itself takes the union and order doesn't matter.
    """
    parts = [p for p in path.split("/") if p]
    parts = parts[:-1]
    out = ["/".join(parts[: i + 1]) for i in range(len(parts) - 1, -1, -1)]
    out.append("")
    return out


def _folder_self_and_ancestors(path: str) -> list[str]:
    """``path`` plus every ancestor folder, deepest first, root last."""
    if not path:
        return [""]
    return _ancestors(f"{path}/_")


def _is_md_page(path: str) -> bool:
    return path.endswith(".md")


# --------------------------------------------------------------------------- #
# Owner repo                                                                  #
# --------------------------------------------------------------------------- #


def _owner_key(path: str) -> str:
    """WikiOwner rows are keyed by the same canonical path as AclEntry rows,
    so owner lookups and grants on the same resource always agree."""
    return _canonicalize(PageKind.of(path), path)


def get_owner(path: str) -> str | None:
    with session() as s:
        row = s.get(WikiOwner, _owner_key(path))
        return row.owner_user_id if row is not None else None


def set_owner(path: str, user_id: str | None) -> None:
    """Insert/update/clear the owner for ``path``.

    ``None`` clears the owner (used during account deletion or as the
    default for pages backfilled at bootstrap when no owner is known).
    """
    key = _owner_key(path)
    with session() as s:
        row = s.get(WikiOwner, key)
        if row is None:
            s.add(WikiOwner(path=key, owner_user_id=user_id))
        else:
            row.owner_user_id = user_id


def transfer_owner(path: str, new_owner_id: str | None) -> None:
    """Transfer ownership, leaving the previous owner as an editor.

    Moves the owner pointer to ``new_owner_id`` and, when there was a
    distinct previous owner, grants them a ``write`` ACL on the path so
    they keep edit access after losing ownership (Google-Docs
    convention). Clearing the owner (``new_owner_id is None``) still
    leaves the prior owner an editor. The grant is idempotent.
    """
    resource_kind = PageKind.of(path)
    canon = _canonicalize(resource_kind, path)
    # Single session so the owner move and the editor grant commit (or roll
    # back) together — otherwise a failed grant after a committed owner move
    # would strand the previous owner with no access.
    with session() as s:
        row = s.get(WikiOwner, canon)
        prev_owner = row.owner_user_id if row is not None else None
        if row is None:
            s.add(WikiOwner(path=canon, owner_user_id=new_owner_id))
        else:
            row.owner_user_id = new_owner_id

        if prev_owner is not None and prev_owner != new_owner_id:
            # Drop any existing grant (read or write) for the previous owner on
            # this path, then add a single write grant — otherwise a pre-existing
            # read row would linger beside the new write row, and the UI (which
            # collapses to the strongest grant) couldn't fully revoke them later.
            s.execute(
                delete(AclEntry).where(
                    AclEntry.resource_kind == resource_kind,
                    AclEntry.resource_path == canon,
                    AclEntry.principal_kind == "user",
                    AclEntry.principal_id == prev_owner,
                )
            )
            s.add(
                AclEntry(
                    id=f"acl_{uuid.uuid4().hex[:12]}",
                    resource_kind=resource_kind,
                    resource_path=canon,
                    principal_kind="user",
                    principal_id=prev_owner,
                    permission="write",
                    granted_by_user_id=new_owner_id,
                )
            )


def group_grant_counts() -> dict[str, dict[str, int]]:
    """Per-group count of granted pages/folders — ACL rows whose principal is a
    group, split by ``resource_kind``. Lives here so all ACL-table reads stay
    inside this module. One aggregate query, no N+1.
    """
    out: dict[str, dict[str, int]] = {}
    with session() as s:
        # COUNT(DISTINCT resource_path): a group can hold both a read and a
        # write row on the same resource — count the resource once, not twice.
        for gid, kind, n in s.execute(
            select(
                AclEntry.principal_id,
                AclEntry.resource_kind,
                func.count(func.distinct(AclEntry.resource_path)),
            )
            .where(AclEntry.principal_kind == "group")
            .group_by(AclEntry.principal_id, AclEntry.resource_kind)
        ).all():
            if gid is None:
                continue
            row = out.setdefault(gid, {"pages": 0, "folders": 0})
            if kind == PageKind.PAGE:
                row["pages"] = int(n)
            elif kind == PageKind.FOLDER:
                row["folders"] = int(n)
    return out


# --------------------------------------------------------------------------- #
# ACL entry repo                                                              #
# --------------------------------------------------------------------------- #


_VALID_RESOURCE_KINDS = {k.value for k in PageKind}
_VALID_PRINCIPAL_KINDS = {"user", "group", "everyone"}
_VALID_PERMISSIONS = {"read", "write"}


def _canonicalize(resource_kind: str, resource_path: str) -> str:
    """Normalize a path for ACL storage.

    Strips leading/trailing slashes. ``""`` is the wiki root (folders
    only — a page can never live at the root). Pages must end in ``.md``.
    """
    p = resource_path.strip().strip("/")
    p = os.path.normpath(p) if p else ""
    if p == ".":
        p = ""
    if resource_kind == PageKind.PAGE and not _is_md_page(p):
        raise ValueError(f"page resource_path must end in .md: {resource_path!r}")
    return p


def _entry_dict(e: AclEntry) -> dict[str, Any]:
    return {
        "id": e.id,
        "resource_kind": e.resource_kind,
        "resource_path": e.resource_path,
        "principal_kind": e.principal_kind,
        "principal_id": e.principal_id,
        "permission": e.permission,
        "granted_by_user_id": e.granted_by_user_id,
        "created_at": e.created_at,
    }


def grant(
    *,
    resource_kind: str,
    resource_path: str,
    principal_kind: str,
    principal_id: str | None,
    permission: str,
    granted_by_user_id: str | None,
) -> str:
    """Insert a grant. Returns the new entry id, or the existing id if
    the same grant is already present (idempotent — the unique constraint
    catches duplicates, but we surface the existing row instead of
    raising)."""
    if resource_kind not in _VALID_RESOURCE_KINDS:
        raise ValueError(f"invalid resource_kind: {resource_kind!r}")
    if principal_kind not in _VALID_PRINCIPAL_KINDS:
        raise ValueError(f"invalid principal_kind: {principal_kind!r}")
    if permission not in _VALID_PERMISSIONS:
        raise ValueError(f"invalid permission: {permission!r}")
    if (principal_kind == "everyone") != (principal_id is None):
        raise ValueError("principal_id must be NULL iff principal_kind == 'everyone'")
    canon = _canonicalize(resource_kind, resource_path)

    with session() as s:
        existing = s.scalar(
            select(AclEntry).where(
                AclEntry.resource_kind == resource_kind,
                AclEntry.resource_path == canon,
                AclEntry.principal_kind == principal_kind,
                AclEntry.principal_id.is_(None)
                if principal_id is None
                else AclEntry.principal_id == principal_id,
                AclEntry.permission == permission,
            )
        )
        if existing is not None:
            return existing.id
        eid = f"acl_{uuid.uuid4().hex[:12]}"
        s.add(
            AclEntry(
                id=eid,
                resource_kind=resource_kind,
                resource_path=canon,
                principal_kind=principal_kind,
                principal_id=principal_id,
                permission=permission,
                granted_by_user_id=granted_by_user_id,
            )
        )
    log.info(
        "acl grant id=%s %s:%s -> %s:%s %s",
        eid,
        resource_kind,
        canon,
        principal_kind,
        principal_id or "*",
        permission,
    )
    return eid


def revoke(entry_id: str) -> None:
    with session() as s:
        e = s.get(AclEntry, entry_id)
        if e is not None:
            s.delete(e)


def list_for_path(path: str) -> list[dict[str, Any]]:
    """All grants that apply to ``path`` — page-level rows first
    (only populated when ``path`` is a page), then folder rows at every
    ancestor in deepest-first order.

    Used by the share dialog to render "what's granted here, and where
    is it inherited from."
    """
    is_page = _is_md_page(path)
    canon = _canonicalize(PageKind.PAGE if is_page else PageKind.FOLDER, path)
    if is_page:
        ancestors = _ancestors(canon)
    else:
        # Folder: walk its own parents. Treat ``canon + "/_"`` as a
        # synthetic page path purely so ``_ancestors`` returns the
        # folder's parents (the ``_`` suffix is dropped by the slice).
        ancestors = _ancestors(canon + "/_") if canon else [""]
        if canon and canon not in ancestors:
            ancestors.insert(0, canon)
    with session() as s:
        page_rows: list[AclEntry] = []
        if is_page:
            page_rows = list(
                s.scalars(
                    select(AclEntry)
                    .where(
                        AclEntry.resource_kind == PageKind.PAGE,
                        AclEntry.resource_path == canon,
                    )
                    .order_by(AclEntry.created_at.asc())
                ).all()
            )
        folder_rows: list[AclEntry] = []
        for f in ancestors:
            folder_rows.extend(
                s.scalars(
                    select(AclEntry)
                    .where(
                        AclEntry.resource_kind == PageKind.FOLDER,
                        AclEntry.resource_path == f,
                    )
                    .order_by(AclEntry.created_at.asc())
                ).all()
            )
    return [_entry_dict(e) for e in page_rows] + [_entry_dict(e) for e in folder_rows]


def list_for_group(group_id: str) -> list[dict[str, Any]]:
    """All ACL rows whose principal is ``group_id`` — the pages and folders
    shared with the group. Ordered folders-then-pages, path-ascending.

    Used by the admin group page to show (and revoke) what a group can
    access. The inverse of ``list_for_path``'s resource-centric view.
    """
    with session() as s:
        rows = list(
            s.scalars(
                select(AclEntry)
                .where(
                    AclEntry.principal_kind == "group",
                    AclEntry.principal_id == group_id,
                )
                .order_by(
                    AclEntry.resource_kind.asc(),
                    AclEntry.resource_path.asc(),
                    AclEntry.created_at.asc(),
                )
            ).all()
        )
    return [_entry_dict(e) for e in rows]


def delete_all_for_path(path: str) -> None:
    """Drop every page-level ACL row keyed at ``path``. Folder rows are
    not touched (a folder grant can outlive any individual page in it).
    Called when a page is deleted."""
    with session() as s:
        s.execute(
            delete(AclEntry).where(
                AclEntry.resource_kind == PageKind.PAGE,
                AclEntry.resource_path == path,
            )
        )
        s.execute(delete(WikiOwner).where(WikiOwner.path == path))


# --------------------------------------------------------------------------- #
# Resolver                                                                    #
# --------------------------------------------------------------------------- #


def effective(
    user_id: str | None,
    is_admin: bool,
    path: str,
) -> set[str]:
    """Return the set of permissions ``user_id`` has on ``path``.

    Steps (mirrors the design doc):
      1. ``is_admin`` → ``{"read", "write"}`` (admin override).
      2. ``user_id == owner(path)`` → ``{"read", "write"}``.
      3. Collect grants:
         - page-level rows at ``path``
         - folder rows at every ancestor (including root ``""``)
         For each row, principal matches if:
           ``principal_kind='everyone'`` OR
           ``user`` AND ``principal_id == user_id`` OR
           ``group`` AND user is a member of that group.
      4. Union the permissions; ``write`` implies ``read``.

    Anonymous callers (``user_id is None``) get only ``everyone`` grants.

    Implicit-public fallback: if the path is **completely unconfigured**
    (no owner row AND zero ACL rows match the page path or any of its
    folder ancestors), the resolver returns ``{"read", "write"}``. This
    covers test setups and seed scripts that bypass the lifecycle hook
    by committing directly with ``git.commit_file``. The first owner
    assignment or ACL grant "manages" the path and the implicit-public
    falls away.
    """
    if is_admin:
        return {"read", "write"}

    raw_path = path.strip().strip("/")
    is_page = _is_md_page(raw_path)
    canon_path = _canonicalize(PageKind.of(raw_path), raw_path)
    folder_paths = (
        _ancestors(canon_path) if is_page else _folder_self_and_ancestors(canon_path)
    )

    owner = get_owner(canon_path)
    if owner is not None and user_id is not None and owner == user_id:
        return {"read", "write"}

    group_ids = groups_repo.group_ids_for_user(user_id) if user_id is not None else []

    with session() as s:
        rows = list(
            s.scalars(
                _grants_for_principal(user_id, group_ids, canon_path, folder_paths)
            ).all()
        )
        if (
            not rows
            and owner is None
            and not _path_is_managed(s, canon_path, folder_paths)
        ):
            return {"read", "write"}

    perms: set[str] = set()
    for r in rows:
        perms.add(r.permission)
    if "write" in perms:
        perms.add("read")
    return perms


def _path_is_managed(s: Any, page_path: str, folder_paths: list[str]) -> bool:
    """True iff *any* ACL row exists at the page path or any folder
    ancestor — irrespective of principal. Used by the resolver and the
    bulk filter to detect "unconfigured" paths so the implicit-public
    fallback only kicks in when no human has ever set policy here."""
    conditions: list[ColumnElement[bool]] = []
    if page_path and _is_md_page(page_path):
        conditions.append(
            and_(
                AclEntry.resource_kind == PageKind.PAGE,
                AclEntry.resource_path == page_path,
            )
        )
    if folder_paths:
        conditions.append(
            and_(
                AclEntry.resource_kind == PageKind.FOLDER,
                AclEntry.resource_path.in_(folder_paths),
            )
        )
    if not conditions:
        return False
    return (
        s.scalar(select(func.count()).select_from(AclEntry).where(or_(*conditions))) > 0
    )


def _grants_for_principal(
    user_id: str | None,
    group_ids: list[str],
    page_path: str,
    folder_paths: list[str],
) -> Select[tuple[AclEntry]]:
    """Build the SELECT that returns every ACL row matching this user."""
    principal_clauses = [AclEntry.principal_kind == "everyone"]
    if user_id is not None:
        principal_clauses.append(
            and_(AclEntry.principal_kind == "user", AclEntry.principal_id == user_id)
        )
    if group_ids:
        principal_clauses.append(
            and_(
                AclEntry.principal_kind == "group",
                AclEntry.principal_id.in_(group_ids),
            )
        )
    resource_clauses: list[ColumnElement[bool]] = []
    if page_path and _is_md_page(page_path):
        resource_clauses.append(
            and_(AclEntry.resource_kind == PageKind.PAGE, AclEntry.resource_path == page_path)
        )
    if folder_paths:
        resource_clauses.append(
            and_(
                AclEntry.resource_kind == PageKind.FOLDER,
                AclEntry.resource_path.in_(folder_paths),
            )
        )
    if not resource_clauses:
        # Should never happen: every resource is either a page (with at least
        # the root ancestor) or a folder (which yields the folder itself and
        # its ancestors). Defensive fallback to avoid a pointless OR () clause.
        resource_clauses.append(literal(False))
    return select(AclEntry).where(or_(*principal_clauses), or_(*resource_clauses))


def can(
    user_id: str | None,
    is_admin: bool,
    action: str,
    path: str,
) -> bool:
    """Convenience wrapper — true iff ``action`` ∈ ``effective(...)``.

    ``action`` is ``"read"`` or ``"write"``.
    """
    return action in effective(user_id, is_admin, path)


# --------------------------------------------------------------------------- #
# Bulk visibility filter for list / search                                    #
# --------------------------------------------------------------------------- #


def visible_paths_filter(
    user_id: str | None,
    is_admin: bool,
    path_column: Any,
) -> Any:
    """Build a SQLAlchemy predicate that filters ``path_column`` to the
    set of pages this caller can read.

    Used by document listing and BM25 search so we don't call ``effective``
    in a loop. Admins get a universal-true predicate.

    Logic (mirrors :func:`effective`):
      - admin → true
      - owner(path) = user → true
      - any matching ACL row at the page path → true
      - any matching ACL row at a folder ancestor of the path
        (root ``''`` matches everything; any non-empty folder path ``F``
        matches when the document path starts with ``F + '/'``)
      - implicit-public fallback: no owner row AND no ACL row anywhere
        on the path → true. Lets pages seeded by tests or scripts that
        bypass the lifecycle hook remain readable.
    """
    if is_admin:
        return literal(True)

    group_ids = groups_repo.group_ids_for_user(user_id) if user_id is not None else []

    principal_clauses = [AclEntry.principal_kind == "everyone"]
    if user_id is not None:
        principal_clauses.append(
            and_(AclEntry.principal_kind == "user", AclEntry.principal_id == user_id)
        )
    if group_ids:
        principal_clauses.append(
            and_(
                AclEntry.principal_kind == "group",
                AclEntry.principal_id.in_(group_ids),
            )
        )
    principal_predicate = or_(*principal_clauses)

    page_match = and_(
        AclEntry.resource_kind == PageKind.PAGE,
        AclEntry.resource_path == path_column,
        principal_predicate,
    )
    folder_match = and_(
        AclEntry.resource_kind == PageKind.FOLDER,
        principal_predicate,
        or_(
            AclEntry.resource_path == "",
            path_column.like(AclEntry.resource_path + "/%"),
        ),
    )
    acl_exists = exists(
        select(1).where(or_(page_match, folder_match)).correlate_except(AclEntry)
    )
    # Implicit-public match: page is unconfigured (no owner, no ACL row
    # anywhere on its path). Mirrors the same fallback in ``effective``.
    any_acl_match = and_(
        or_(
            and_(
                AclEntry.resource_kind == PageKind.PAGE,
                AclEntry.resource_path == path_column,
            ),
            and_(
                AclEntry.resource_kind == PageKind.FOLDER,
                or_(
                    AclEntry.resource_path == "",
                    path_column.like(AclEntry.resource_path + "/%"),
                ),
            ),
        ),
    )
    any_acl_exists = exists(select(1).where(any_acl_match).correlate_except(AclEntry))
    any_owner_exists = exists(
        select(1)
        .select_from(WikiOwner)
        .where(WikiOwner.path == path_column)
        .correlate_except(WikiOwner)
    )
    implicit_public = and_(~any_acl_exists, ~any_owner_exists)

    if user_id is None:
        return or_(acl_exists, implicit_public)
    owner_match = exists(
        select(1)
        .select_from(WikiOwner)
        .where(WikiOwner.path == path_column, WikiOwner.owner_user_id == user_id)
        .correlate_except(WikiOwner)
    )
    return or_(owner_match, acl_exists, implicit_public)


def filter_paths_in_python(
    user_id: str | None,
    is_admin: bool,
    paths: Iterable[str],
) -> list[str]:
    """Tiny Python-side filter — for code paths that already have a
    list of paths in memory and don't want to roundtrip to SQL.

    Used by the documents listing endpoint: ``git ls-files`` returns the
    full set, and we filter to the visible subset here.
    """
    if is_admin:
        return list(paths)
    return [p for p in paths if can(user_id, False, "read", p)]


# --------------------------------------------------------------------------- #
# Lifecycle hooks (called from app.wiki.notify)                               #
# --------------------------------------------------------------------------- #


def on_page_created(path: str, owner_user_id: str | None) -> None:
    """Stamp owner + seed default-public ACL rows.

    Idempotent: if the page already has either an owner row or any
    page-level ACL row, the operation is a no-op for that piece. Lets
    us call this from the bootstrap walk and from the live create path
    without coordinating order.
    """
    if not _is_md_page(path):
        return
    canon = _canonicalize(PageKind.PAGE, path)
    with session() as s:
        if s.get(WikiOwner, canon) is None:
            s.add(WikiOwner(path=canon, owner_user_id=owner_user_id))
        existing = (
            s.scalar(
                select(func.count())
                .select_from(AclEntry)
                .where(
                    AclEntry.resource_kind == PageKind.PAGE,
                    AclEntry.resource_path == canon,
                    AclEntry.principal_kind == "everyone",
                )
            )
            or 0
        )
        if existing == 0:
            for perm in ("read", "write"):
                s.add(
                    AclEntry(
                        id=f"acl_{uuid.uuid4().hex[:12]}",
                        resource_kind=PageKind.PAGE,
                        resource_path=canon,
                        principal_kind="everyone",
                        principal_id=None,
                        permission=perm,
                        granted_by_user_id=owner_user_id,
                    )
                )


def on_page_deleted(path: str) -> None:
    """Remove owner + all page-level ACL rows for ``path``."""
    if not _is_md_page(path):
        return
    canon = _canonicalize(PageKind.PAGE, path)
    delete_all_for_path(canon)


def on_path_moved(moves: list[PathMove], root_move: PathMove | None = None) -> None:
    """Rewrite ``acl_entries.resource_path`` and ``wiki_owners.path`` for
    every ``(old, new)`` pair from one ``git mv`` commit.

    Folder ACLs at the *moved subtree's* paths are also rewritten — if
    a directory ``a/`` is renamed to ``b/``, an existing folder grant
    at ``a/sub`` becomes ``b/sub``. We do this with a row-by-row
    rewrite per move; the volume is tiny in practice.

    ``root_move`` is the rename as the caller issued it (the folder itself for
    a directory move). Prefer it for the folder-prefix rewrite: inferring the
    prefix from the file moves via ``common_folder_rename`` finds the *deepest*
    shared prefix, which misses the renamed folder's own grant when all its
    files sit in one subdirectory (e.g. moving ``a`` whose only file is
    ``a/sub/x.md``). Falls back to inference when not supplied.
    """
    if not moves:
        return
    with session() as s:
        for mv in moves:
            old_p, new_p = mv.old, mv.new
            if _is_md_page(old_p):
                # Page move: page-level ACLs + owner row.
                s.execute(
                    update(AclEntry)
                    .where(
                        AclEntry.resource_kind == PageKind.PAGE,
                        AclEntry.resource_path == old_p,
                    )
                    .values(resource_path=new_p)
                )
                s.execute(
                    update(WikiOwner).where(WikiOwner.path == old_p).values(path=new_p)
                )

        # The folder-level prefix swap. A `.md`-page root is a single page
        # move: the per-file loop above re-keys it and there is no folder to
        # prefix-rewrite. Only a folder root drives the swap. Legacy callers
        # without a root_move fall back to inferring the prefix from the shared
        # prefix of the file moves — which can under-reach (see docstring) and
        # can't tell a single cross-folder file move from a folder rename.
        if root_move is not None:
            old_prefix, new_prefix = (
                (None, None)
                if _is_md_page(root_move.old)
                else (root_move.old, root_move.new)
            )
        else:
            old_prefix, new_prefix = common_folder_rename(moves)
        if old_prefix is not None and new_prefix is not None:
            # Folder ACL at the renamed folder itself.
            s.execute(
                update(AclEntry)
                .where(
                    AclEntry.resource_kind == PageKind.FOLDER,
                    AclEntry.resource_path == old_prefix,
                )
                .values(resource_path=new_prefix)
            )
            # Folder ACLs nested under the renamed folder.
            s.execute(
                update(AclEntry)
                .where(
                    AclEntry.resource_kind == PageKind.FOLDER,
                    AclEntry.resource_path.like(old_prefix + "/%"),
                )
                .values(
                    resource_path=func.concat(
                        new_prefix,
                        func.substr(AclEntry.resource_path, len(old_prefix) + 1),
                    )
                )
            )
            # Owner rows for the renamed folder and folders nested under it.
            # Page owner rows were already re-keyed by the per-file loop, so
            # the prefix match only finds folder rows (a page row still at an
            # old path would be an orphan the rewrite also heals).
            s.execute(
                update(WikiOwner)
                .where(WikiOwner.path == old_prefix)
                .values(path=new_prefix)
            )
            s.execute(
                update(WikiOwner)
                .where(WikiOwner.path.like(old_prefix + "/%"))
                .values(
                    path=func.concat(
                        new_prefix,
                        func.substr(WikiOwner.path, len(old_prefix) + 1),
                    )
                )
            )


# Re-export for type-stability of ``Document`` import — keeps pyright happy
# when this module is the only consumer.
__all__ = [
    "can",
    "delete_all_for_path",
    "effective",
    "filter_paths_in_python",
    "get_owner",
    "grant",
    "list_for_path",
    "on_page_created",
    "on_page_deleted",
    "on_path_moved",
    "revoke",
    "set_owner",
    "transfer_owner",
    "visible_paths_filter",
]
