"""Update-policy repo — per-page / per-folder control over wiki auto-updates.

Postgres-only governance metadata keyed by path (a ``.md`` page, a folder, or
``""`` for the wiki root), mirroring ``app/wiki/acl.py``. Two independent
settings — ``ingestion_auto_update_disabled`` (tri-state) and
``update_instruction`` — are each resolved most-granular-wins by walking a path
and its ancestor folders. See the design page
``Engineering Projects/Agent Wiki Project/design/Update Policy.md``.

Free functions over the ``UpdatePolicy`` model; each opens its own session and
returns plain dicts so callers don't depend on the ORM.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, update

from app.db.models import UpdatePolicy
from app.db.session import session
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


def _now() -> str:
    """UTC timestamp matching the ``YYYY-MM-DD HH:MM:SS`` column format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def kind_for_path(path: str) -> str:
    """A ``.md`` path is a ``page``; everything else (incl. root) is a ``folder``."""
    return "page" if path.endswith(".md") else "folder"


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
        "updated_by_user_id": row.updated_by_user_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def get(path: str) -> dict[str, Any] | None:
    """The explicit policy row for exactly this path, or ``None``."""
    with session() as s:
        row = s.get(UpdatePolicy, normalize_path(path))
        return _to_dict(row) if row is not None else None


def set_policy(
    path: str,
    *,
    ingestion_auto_update_disabled: bool | None | _UnsetType = _UNSET,
    update_instruction: str | None | _UnsetType = _UNSET,
    actor_user_id: str | None = None,
) -> dict[str, Any] | None:
    """Upsert the policy for ``path`` with patch semantics.

    Only fields passed (not ``_UNSET``) are changed. An empty-string
    ``update_instruction`` clears it. When the row ends up carrying no setting
    (both fields NULL/empty) it is deleted and ``None`` is returned.
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

        if row.ingestion_auto_update_disabled is None and not row.update_instruction:
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


def on_page_deleted(path: str) -> None:
    """Drop the policy row for a deleted page (mirrors ``acl.on_page_deleted``).

    Called per ``.md`` page from ``app.wiki.notify.after_doc_delete``. Folder
    policy rows are left alone — a folder grant can outlive any single page in
    it, matching the ACL convention.
    """
    delete(path)


def on_path_moved(moves: list[tuple[str, str]]) -> None:
    """Re-key policy rows so a page/folder policy follows a move/rename.

    For each ``(old, new)`` pair the exact row at ``old`` is repointed to
    ``new`` (page rows). When the pairs describe a directory rename, the
    folder's own row and every row nested under it are repointed too — so
    renaming ``a`` to ``b`` carries ``a`` and ``a/sub`` along. Mirrors
    ``acl.on_path_moved``; the row volume is tiny in practice.
    """
    if not moves:
        return
    with session() as s:
        for old_p, new_p in moves:
            s.execute(
                update(UpdatePolicy)
                .where(UpdatePolicy.path == old_p)
                .values(path=new_p)
            )
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


def resolve_for_path(path: str) -> ResolvedPolicy:
    """Effective policy for ``path``: most-granular scope that sets a field wins.

    ``path`` may be a doc or a folder. Walks the path and its ancestor folders,
    closest first, and resolves each field independently — so a page can
    re-enable ingestion under a disabled folder, and a folder instruction
    applies until a nearer scope overrides it.
    """
    norm = normalize_path(path)
    candidates: list[str] = [norm]
    for parent in filesystem.parent_dirs(norm):
        if parent not in candidates:
            candidates.append(parent)

    with session() as s:
        rows = (
            s.execute(select(UpdatePolicy).where(UpdatePolicy.path.in_(candidates)))
            .scalars()
            .all()
        )
    by_path = {r.path: r for r in rows}

    disabled: bool | None = None
    instruction: str | None = None
    for scope in candidates:  # closest first
        row = by_path.get(scope)
        if row is None:
            continue
        if disabled is None and row.ingestion_auto_update_disabled is not None:
            disabled = row.ingestion_auto_update_disabled
        if instruction is None and row.update_instruction:
            instruction = row.update_instruction
        if disabled is not None and instruction is not None:
            break

    return ResolvedPolicy(
        ingestion_auto_update_disabled=bool(disabled),
        update_instruction=instruction,
    )


def is_ingest_disabled(path: str) -> bool:
    """Convenience: is connector/ingest auto-update disabled for ``path``?"""
    return resolve_for_path(path).ingestion_auto_update_disabled
