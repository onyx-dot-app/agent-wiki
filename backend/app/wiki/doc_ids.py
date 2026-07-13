"""Stable ids for wiki pages and folders — the ``wiki_doc_ids`` repo.

Git has no native file identity, so ids are minted here (Postgres-only,
like the ACL tables) and maintained at the wiki lifecycle seams: create
mints, move re-keys the path in place, delete stamps ``deleted_at`` (the
row is kept so the id still resolves — to a tombstone — and a restore
re-binds it). A page recreated at a previously-deleted path is a new
document with a fresh id; the partial unique index on live ``path`` rows
enforces that at most one live row occupies a path.

Backfill for pre-existing content is lazy: reads mint missing rows via
:func:`get_or_mint`, and the hourly BM25 reconcile sweep calls
:func:`ensure_for_paths` for recently-touched pages.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.db.models import WikiDocId
from app.db.session import session
from app.models.wiki import PathMove
from app.wiki import filesystem, git as wiki_git

log = logging.getLogger(__name__)


def _mint_id() -> str:
    return uuid.uuid4().hex[:16]


def _now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _kind_for(path: str) -> str:
    return "page" if path.endswith(".md") else "folder"


def _row_dict(row: WikiDocId) -> dict[str, str | None]:
    return {
        "id": row.id,
        "path": row.path,
        "kind": row.kind,
        "created_at": row.created_at,
        "deleted_at": row.deleted_at,
    }


def get(doc_id: str) -> dict[str, str | None] | None:
    """The row for ``doc_id`` (live or tombstone), or ``None``."""
    with session() as s:
        row = s.get(WikiDocId, doc_id)
        return _row_dict(row) if row else None


def id_for_path(path: str) -> str | None:
    """Id of the live row at ``path``, or ``None``."""
    with session() as s:
        row = s.execute(
            select(WikiDocId).where(WikiDocId.path == path, WikiDocId.deleted_at.is_(None))
        ).scalar_one_or_none()
        return row.id if row else None


def ids_for_paths(paths: list[str]) -> dict[str, str]:
    """``{path: id}`` for the live rows among ``paths``.

    Paths without a live row are simply absent from the result — callers
    building navigation links tolerate a missing id (fall back to the path).
    Used by the listing/search endpoints to attach stable ids in bulk rather
    than resolving one path at a time.

    The lookup is chunked so a whole-tree listing can't emit one ``IN (...)``
    with an unbounded bind-parameter count (parse/plan cost grows with it);
    every path is still returned, just across a bounded number of statements.
    """
    if not paths:
        return {}
    out: dict[str, str] = {}
    with session() as s:
        for i in range(0, len(paths), _ID_LOOKUP_CHUNK):
            chunk = paths[i : i + _ID_LOOKUP_CHUNK]
            rows = s.execute(
                select(WikiDocId.path, WikiDocId.id).where(
                    WikiDocId.path.in_(chunk), WikiDocId.deleted_at.is_(None)
                )
            ).all()
            out.update({path: doc_id for path, doc_id in rows})
    return out


# Max paths per ``IN (...)`` in ``ids_for_paths`` — bounds per-statement
# parse/plan cost on a large tree without a round-trip per path.
_ID_LOOKUP_CHUNK = 1000


def get_or_mint(path: str) -> str:
    """Id of the live row at ``path``, minting one if absent.

    Kind is derived from the path (``.md`` → page, else folder). Safe under
    concurrency: a losing racer re-reads the winner's row.
    """
    existing = id_for_path(path)
    if existing is not None:
        return existing
    new_id = _mint_id()
    try:
        with session() as s:
            s.add(WikiDocId(id=new_id, path=path, kind=_kind_for(path)))
    except IntegrityError:
        # A concurrent writer minted first — theirs wins.
        winner = id_for_path(path)
        if winner is not None:
            return winner
        raise
    return new_id


def mint_for_page(path: str) -> str:
    """Mint (or fetch) the page's id, plus rows for its ancestor folders.

    Folder rows have no create hook of their own when a folder springs into
    existence implicitly with its first page, so page creation seeds them.
    """
    parts = path.split("/")[:-1]
    for i in range(1, len(parts) + 1):
        get_or_mint("/".join(parts[:i]))
    return get_or_mint(path)


def ensure_for_paths(paths: list[str]) -> None:
    """Backfill: mint live rows for any of ``paths`` that lack one. Pages
    also seed their ancestor folders. Never raises — backfill must not
    abort its caller (the startup reindex sweep)."""
    for p in paths:
        try:
            if p.endswith(".md"):
                mint_for_page(p)
            else:
                get_or_mint(p)
        except Exception:
            log.exception("doc_ids backfill failed for %s", p)


def backfill_all() -> None:
    """Mint ids for every tracked page and folder that lacks a live row.

    Run once at boot so an existing wiki (created before stable ids) gets ids
    for all its pages *and folders* — not just the pages someone happens to
    open. Gated on a cheap count so a fully-minted wiki does almost no work;
    otherwise mints the missing rows (``get_or_mint`` skips those that exist).

    Folders are enumerated from the tracked-file prefixes so empty folders
    (only a ``.gitkeep``) get ids too, which page-ancestor seeding misses.
    """
    files = wiki_git.list_paths()
    md = [p for p in files if p.endswith(".md")]
    folders: set[str] = set()
    for f in files:
        parts = f.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            folders.add("/".join(parts[:i]))
    want = len(folders | set(md))
    with session() as s:
        have = s.execute(
            select(func.count())
            .select_from(WikiDocId)
            .where(WikiDocId.deleted_at.is_(None))
        ).scalar_one()
    if have >= want:
        return
    log.info("doc_ids backfill: %d live rows < %d tracked paths; minting missing", have, want)
    ensure_for_paths([*sorted(folders), *md])


def on_path_moved(moves: list[PathMove], root_move: PathMove | None = None) -> None:
    """Re-key live rows so ids follow a move/rename.

    Page rows are re-pointed pair-by-pair; a directory rename additionally
    re-points the folder's own row and every nested row via prefix rewrite.
    ``root_move`` is the rename as the caller issued it (the folder itself
    for a directory move) — without it the folder prefix is inferred via
    ``common_folder_rename``, which resolves to the *deepest* consistent
    prefix and so can miss the renamed folder's own row when its files all
    sit in one subdirectory. A page renamed *out of* ``.md``-space stops
    being a document — its row is stamped deleted.
    """
    if not moves and root_move is None:
        return
    with session() as s:
        for mv in moves:
            if mv.old.endswith(".md") and mv.new.endswith(".md"):
                s.execute(
                    update(WikiDocId)
                    .where(WikiDocId.path == mv.old, WikiDocId.deleted_at.is_(None))
                    .values(path=mv.new)
                )
            elif mv.old.endswith(".md"):
                s.execute(
                    update(WikiDocId)
                    .where(WikiDocId.path == mv.old, WikiDocId.deleted_at.is_(None))
                    .values(deleted_at=_now_text())
                )
        if root_move is not None and not root_move.old.endswith(".md"):
            old_prefix, new_prefix = root_move.old, root_move.new
        else:
            old_prefix, new_prefix = filesystem.common_folder_rename(moves)
        if old_prefix is not None and new_prefix is not None:
            s.execute(
                update(WikiDocId)
                .where(WikiDocId.path == old_prefix, WikiDocId.deleted_at.is_(None))
                .values(path=new_prefix)
            )
            s.execute(
                update(WikiDocId)
                .where(
                    WikiDocId.path.like(old_prefix + "/%"),
                    WikiDocId.deleted_at.is_(None),
                )
                .values(
                    path=func.concat(
                        new_prefix,
                        func.substr(WikiDocId.path, len(old_prefix) + 1),
                    )
                )
            )


def on_deleted(path: str) -> None:
    """Stamp ``deleted_at`` on the live row at ``path`` and, for a folder,
    every live row nested under it. Rows are kept — the id still resolves."""
    with session() as s:
        s.execute(
            update(WikiDocId)
            .where(
                WikiDocId.deleted_at.is_(None),
                (WikiDocId.path == path) | WikiDocId.path.like(path + "/%"),
            )
            .values(deleted_at=_now_text())
        )


def on_restored(paths: list[str]) -> None:
    """Re-bind ids after a restore: for each path, resurrect the most
    recently deleted tombstone row (clear ``deleted_at``) unless a live row
    already occupies the path."""
    with session() as s:
        for p in paths:
            live = s.execute(
                select(WikiDocId.id).where(
                    WikiDocId.path == p, WikiDocId.deleted_at.is_(None)
                )
            ).scalar_one_or_none()
            if live is not None:
                continue
            row = s.execute(
                select(WikiDocId)
                .where(WikiDocId.path == p, WikiDocId.deleted_at.is_not(None))
                .order_by(WikiDocId.deleted_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if row is not None:
                row.deleted_at = None
