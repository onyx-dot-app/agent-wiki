"""Update-policy repo — per-page / per-folder control over wiki auto-updates.

Postgres-only governance metadata keyed by path (a ``.md`` page, a folder, or
``""`` for the wiki root), mirroring ``app/wiki/acl.py``. Three independent
settings — ``ingestion_auto_update_disabled`` (tri-state),
``update_instruction``, and ``ai_management_allowed`` (tri-state) — are each
resolved most-granular-wins by walking a path
and its ancestor folders. See the design page
``Engineering Projects/Agent Wiki Project/design/Update Policy.md``.

Free functions over the ``UpdatePolicy`` model; each opens its own session and
returns plain dicts so callers don't depend on the ORM.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from collections.abc import Callable, Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, update

from app.db.models import UpdatePolicy
from app.models.wiki import PageKind, PathMove
from app.db.session import session
from app.ingest import settings as ingest_settings
from app.wiki import filesystem

log = logging.getLogger(__name__)

# Sentinel for "field not provided" in ``set_policy``. ``None`` and ``""`` are
# meaningful (clear the field), so they can't double as the no-op marker. A
# typed sentinel (not bare ``object()``) keeps call-site type checking intact.
class _UnsetType:
    """Sentinel type for a ``set_policy`` field that was not provided."""


_UNSET = _UnsetType()


class ResolvedPolicy(BaseModel):
    """The effective policy for a path after the most-granular-wins cascade."""

    model_config = ConfigDict(frozen=True)

    ingestion_auto_update_disabled: bool = False
    update_instruction: str | None = None
    # Effective default is False: without an explicit opt-in somewhere in the
    # scope chain, AI auto-management stays propose -> approve.
    ai_management_allowed: bool = False


def _now() -> str:
    """UTC timestamp matching the ``YYYY-MM-DD HH:MM:SS`` column format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def kind_for_path(path: str) -> PageKind:
    """A ``.md`` path is a ``page``; everything else (incl. root) is a ``folder``."""
    return PageKind.of(path)


def normalize_path(raw: str) -> str:
    """Canonicalize a policy path. ``""`` and ``"/"`` both mean the wiki root."""
    stripped = raw.strip()
    if stripped in ("", "/"):
        return ""
    return filesystem.safe_rel_path(stripped.lstrip("/"))


def _to_dict(row: UpdatePolicy) -> dict[str, Any]:
    return {
        "path": row.path,
        "kind": row.kind,
        "ingestion_auto_update_disabled": row.ingestion_auto_update_disabled,
        "update_instruction": row.update_instruction,
        "ai_management_allowed": row.ai_management_allowed,
        "warn_update_threshold": row.warn_update_threshold,
        "updated_by_user_id": row.updated_by_user_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def get(path: str) -> dict[str, Any] | None:
    """The explicit policy row for exactly this path, or ``None``."""
    with session() as s:
        row = s.get(UpdatePolicy, normalize_path(path))
        return _to_dict(row) if row is not None else None


def resolve_warn_threshold(path: str) -> int:
    """Effective too-frequent-update warning threshold for ``path``.

    The page's own ``warn_update_threshold`` if set, else the wiki-wide default
    (``ingest_settings.warn_update_threshold_default``). Per-page only, unlike
    the two cascaded fields it does not walk ancestor folders. ``0`` warns
    on every auto-update.
    """
    with session() as s:
        row = s.get(UpdatePolicy, normalize_path(path))
        if row is not None and row.warn_update_threshold is not None:
            return row.warn_update_threshold
    return ingest_settings.get().warn_update_threshold_default


def set_policy(
    path: str,
    *,
    ingestion_auto_update_disabled: bool | None | _UnsetType = _UNSET,
    update_instruction: str | None | _UnsetType = _UNSET,
    ai_management_allowed: bool | None | _UnsetType = _UNSET,
    warn_update_threshold: int | None | _UnsetType = _UNSET,
    actor_user_id: str | None = None,
) -> dict[str, Any] | None:
    """Upsert the policy for ``path`` with patch semantics.

    Only fields passed (not ``_UNSET``) are changed. An empty-string
    ``update_instruction`` clears it. When the row ends up carrying no setting
    (every field NULL/empty) it is deleted and ``None`` is returned.
    """
    norm = normalize_path(path)
    with session() as s:
        row = s.get(UpdatePolicy, norm)
        existed = row is not None
        if row is None:
            row = UpdatePolicy(path=norm, kind=kind_for_path(norm))

        if not isinstance(ingestion_auto_update_disabled, _UnsetType):
            row.ingestion_auto_update_disabled = ingestion_auto_update_disabled
        if not isinstance(update_instruction, _UnsetType):
            row.update_instruction = update_instruction or None
        if not isinstance(ai_management_allowed, _UnsetType):
            row.ai_management_allowed = ai_management_allowed
        if not isinstance(warn_update_threshold, _UnsetType):
            row.warn_update_threshold = warn_update_threshold

        if (
            row.ingestion_auto_update_disabled is None
            and not row.update_instruction
            and row.ai_management_allowed is None
            and row.warn_update_threshold is None
        ):
            if existed:
                s.delete(row)
            return None

        now = _now()
        row.updated_by_user_id = actor_user_id
        row.updated_at = now
        if not existed:
            row.created_at = now
            s.add(row)
        return _to_dict(row)


def delete(path: str) -> bool:
    """Remove the policy row for ``path``. Returns whether a row was deleted."""
    with session() as s:
        row = s.get(UpdatePolicy, normalize_path(path))
        if row is None:
            return False
        s.delete(row)
        return True


def delete_at(path: str) -> bool:
    """Delete the policy row stored at *exactly* ``path``, without normalization.

    For internal cleanup of rows parked at non-canonical keys that ``delete``'s
    ``safe_rel_path`` would reject — notably ``.trash/…`` rows re-pointed there by
    a trash move (see ``app/wiki/trash.py:purge``). Returns whether a row went."""
    with session() as s:
        row = s.get(UpdatePolicy, path)
        if row is None:
            return False
        s.delete(row)
        return True


def on_page_deleted(path: str) -> None:
    """Drop the policy row for a deleted page (mirrors ``acl.on_page_deleted``).

    Called per ``.md`` page from ``app.wiki.notify.after_doc_delete``. Folder
    policy rows are left alone — a folder grant can outlive any single page in
    it, matching the ACL convention.
    """
    delete(path)


def on_path_moved(moves: list[PathMove], root_move: PathMove | None = None) -> None:
    """Re-key policy rows so a page/folder policy follows a move/rename.

    For each ``(old, new)`` pair the exact row at ``old`` is repointed to
    ``new`` (page rows). When the pairs describe a directory rename, the
    folder's own row and every row nested under it are repointed too — so
    renaming ``a`` to ``b`` carries ``a`` and ``a/sub`` along. Mirrors
    ``acl.on_path_moved``; the row volume is tiny in practice.

    ``root_move`` is the caller's actual folder rename; prefer it over inferring
    the prefix from the file moves (which finds the deepest shared prefix and
    can miss the folder's own row). See ``acl.on_path_moved``.
    """
    if not moves:
        return
    with session() as s:
        for mv in moves:
            s.execute(
                update(UpdatePolicy)
                .where(UpdatePolicy.path == mv.old)
                .values(path=mv.new)
            )
        if root_move is not None:
            # A `.md`-page root is a single page move: the per-file loop above
            # re-keys it and there is no folder-prefix rewrite. Only a folder
            # root drives the prefix swap. (Inferring the prefix from the file
            # moves alone can't tell a single cross-folder file move from a
            # folder rename, and would rewrite the source folder's row onto the
            # destination's.)
            old_prefix, new_prefix = (
                (None, None)
                if root_move.old.endswith(".md")
                else (root_move.old, root_move.new)
            )
        else:
            old_prefix, new_prefix = filesystem.common_folder_rename(moves)
        if old_prefix is not None and new_prefix is not None:
            # The renamed folder's own policy row.
            s.execute(
                update(UpdatePolicy)
                .where(UpdatePolicy.path == old_prefix)
                .values(path=new_prefix)
            )
            # Policy rows nested under the renamed folder.
            s.execute(
                update(UpdatePolicy)
                .where(UpdatePolicy.path.like(old_prefix + "/%"))
                .values(
                    path=func.concat(
                        new_prefix,
                        func.substr(UpdatePolicy.path, len(old_prefix) + 1),
                    )
                )
            )


def _scope_chain(norm: str) -> list[str]:
    """The path itself then its ancestor folders, closest first (root last)."""
    chain: list[str] = [norm]
    for parent in filesystem.parent_dirs(norm):
        if parent not in chain:
            chain.append(parent)
    return chain


def _resolve_chain(chain: list[str], by_path: dict[str, UpdatePolicy]) -> ResolvedPolicy:
    """Resolve one scope chain (closest first) against pre-fetched rows."""
    disabled: bool | None = None
    instruction: str | None = None
    ai_managed: bool | None = None
    for scope in chain:
        row = by_path.get(scope)
        if row is None:
            continue
        if disabled is None and row.ingestion_auto_update_disabled is not None:
            disabled = row.ingestion_auto_update_disabled
        if instruction is None and row.update_instruction:
            instruction = row.update_instruction
        if ai_managed is None and row.ai_management_allowed is not None:
            ai_managed = row.ai_management_allowed
        if disabled is not None and instruction is not None and ai_managed is not None:
            break
    return ResolvedPolicy(
        ingestion_auto_update_disabled=bool(disabled),
        update_instruction=instruction,
        ai_management_allowed=bool(ai_managed),
    )


def resolve_ai_management_for_paths(paths: Iterable[str]) -> dict[str, bool | None]:
    """Effective ``ai_management_allowed`` **tri-state** for many paths in a
    single query (see ``resolve_ai_management`` for the tri-state meaning).

    Fetches the union of every path's scope chain once, then resolves each path
    most-granular-wins. Keyed by the *input* path strings. Use this instead of
    per-path calls when checking a batch of proposal paths."""
    chains = {p: _scope_chain(normalize_path(p)) for p in paths}
    if not chains:
        return {}
    scopes = {scope for chain in chains.values() for scope in chain}
    with session() as s:
        rows = s.scalars(
            select(UpdatePolicy).where(UpdatePolicy.path.in_(scopes))
        ).all()
    by_path = {r.path: r for r in rows}
    result: dict[str, bool | None] = {}
    for p, chain in chains.items():
        value: bool | None = None
        for scope in chain:
            row = by_path.get(scope)
            if row is not None and row.ai_management_allowed is not None:
                value = row.ai_management_allowed
                break
        result[p] = value
    return result


def resolve_ai_management(path: str) -> bool | None:
    """Effective ``ai_management_allowed`` **tri-state** for ``path``.

    ``None`` = unset everywhere in the scope chain (so AI management stays
    propose→approve); ``True`` = opted in (may auto-apply); ``False`` =
    explicitly forbidden (also the do-not-consolidate marker). Distinct from
    ``ResolvedPolicy.ai_management_allowed``, which collapses to a bool and so
    can't tell "forbidden" from "unset" — detection needs that difference to
    skip forbidden scopes while still proposing on unset ones."""
    return resolve_ai_management_for_paths([path]).get(path)


def resolve_for_paths(paths: Iterable[str]) -> dict[str, ResolvedPolicy]:
    """Effective policy for many paths in a single query.

    Fetches the union of every path's scope chain once, then resolves each path
    most-granular-wins (each field independently). Keyed by the *input* path
    strings. Use this on the ingest hot path instead of per-candidate calls.
    """
    chains = {p: _scope_chain(normalize_path(p)) for p in paths}
    scopes = {scope for chain in chains.values() for scope in chain}
    if not scopes:
        return {}

    with session() as s:
        rows = (
            s.execute(select(UpdatePolicy).where(UpdatePolicy.path.in_(scopes)))
            .scalars()
            .all()
        )
    by_path = {r.path: r for r in rows}
    return {orig: _resolve_chain(chain, by_path) for orig, chain in chains.items()}


def resolve_for_path(path: str) -> ResolvedPolicy:
    """Effective policy for a single ``path`` (convenience over
    :func:`resolve_for_paths`): most-granular scope that sets a field wins,
    walking the path then its ancestor folders, each field resolved
    independently — so a page can re-enable ingestion under a disabled folder.
    """
    return resolve_for_paths([path]).get(path, ResolvedPolicy())


def is_ingest_disabled(path: str) -> bool:
    """Convenience: is connector/ingest auto-update disabled for ``path``?"""
    return resolve_for_path(path).ingestion_auto_update_disabled


def is_ai_management_allowed(path: str) -> bool:
    """Convenience: may the AI auto-manage ``path`` (effective, cascaded)?"""
    return resolve_for_path(path).ai_management_allowed


def disabled_paths(paths: Iterable[str]) -> set[str]:
    """Subset of ``paths`` whose effective policy disables ingestion auto-update
    (one query, via :func:`resolve_for_paths`). Keyed by the input path strings."""
    return {
        p
        for p, r in resolve_for_paths(paths).items()
        if r.ingestion_auto_update_disabled
    }


def _disabled_true_scopes() -> list[str]:
    """Paths that *explicitly* disable ingestion auto-update (the True rows).

    Small — only managed scopes, not pages. Drives the auto-update-enabled
    metric without enumerating every wiki page.

    Trashed scopes are excluded: a deleted page keeps its policy row (restore
    brings the policy back), but a ``.trash/`` path can't affect any live page
    — and ``kind_for_path`` rejects trash paths, which would abort the whole
    count and make the metric silently fall back to "all enabled".
    """
    with session() as s:
        rows = s.execute(
            select(UpdatePolicy.path).where(
                UpdatePolicy.ingestion_auto_update_disabled.is_(True)
            )
        ).scalars().all()
    return [p for p in rows if not filesystem.is_trash_path(p)]


def count_ingest_enabled_pages(
    total_pages: int, list_pages_under: Callable[[str], list[str]]
) -> int:
    """Number of pages with ingestion auto-update **enabled** (effective).

    Driven by the small set of explicitly-disabled scopes, not by enumerating
    every page: only the protected subtrees are listed (via
    ``list_pages_under(prefix)``, injected to keep the search index out of this
    module), then resolved most-granular-wins so re-enabled children are counted
    back as enabled. Pages under no disabled scope are enabled by definition.

    Clamped to ``[0, total_pages]``: a disabled *page* scope is counted even if
    that path isn't indexed (a stale or pre-index policy row), so the subtracted
    set can exceed ``total_pages`` when the policy table and the index disagree —
    the gauge must never read negative.
    """
    scopes = _disabled_true_scopes()
    if not scopes:
        return total_pages
    candidates: set[str] = set()
    for scope in scopes:
        if kind_for_path(scope) == PageKind.PAGE:
            candidates.add(scope)
        else:
            candidates.update(list_pages_under(f"{scope}/" if scope else ""))
    if not candidates:
        return total_pages
    return max(0, total_pages - len(disabled_paths(candidates)))
