"""The Trash — soft-deleted wiki items, derived entirely from git.

Deleting a page/folder is a **move** into a hidden ``.trash/<trash_id>/<path>``
(see ``app/api/wiki.py`` delete + ``git.restore_from_trash``), not a ``git rm``.
Because it's a move, ``notify.after_path_move`` re-points every path-keyed row
(ACL, owner, policy, comments, …) to the trash location — so restore is
lossless. Nothing about the trash is stored in Postgres: the ``.trash/`` tree is
the source of truth, and the original path is encoded in the trash location
(strip the ``.trash/<trash_id>/`` prefix). Who/when come from the move commit.

``.trash/`` is excluded from every path enumerator and rejected by
``filesystem.safe_rel_path``, so trashed content is unreachable except through
this module and the Trash/restore endpoints.
"""

from __future__ import annotations

import os
import uuid

from pydantic import BaseModel

from app.models.wiki import PageKind
from app.wiki import acl, git as wiki_git, update_policy
from app.wiki.filesystem import TRASH_DIR


class TrashEntry(BaseModel):
    """One trashed item (a page, or a folder root) for the Trash view."""

    trash_id: str
    original_path: str  # where it lived; restore moves it back here
    kind: PageKind
    trashed_by: str  # git author of the trash-move commit
    trashed_at: str  # ISO-8601


def new_trash_id() -> str:
    return uuid.uuid4().hex[:12]


def trash_location(trash_id: str, original_path: str) -> str:
    """The ``.trash/`` path an item at ``original_path`` moves to when trashed."""
    return f"{TRASH_DIR}/{trash_id}/{original_path}"


_ORIGINAL_TRAILER = "Trash-Original:"


def trash_commit_message(original_path: str) -> str:
    """Commit message for a trash-move.

    The subject (``trash <path>``) is for humans; the ``Trash-Original`` trailer
    records the *root* that was trashed so the Trash view can classify it. The
    ``.trash/`` tree alone can't: trashing a page ``p/a.md`` and trashing a
    folder ``p/`` whose only file is ``a.md`` both land as ``.trash/<id>/p/a.md``
    — byte-identical trees. Only the deleter knows which it was, so we record it.
    """
    return f"trash {original_path}\n\n{_ORIGINAL_TRAILER} {original_path}"


def _original_from_message(message: str) -> str | None:
    for line in message.splitlines():
        if line.startswith(_ORIGINAL_TRAILER):
            return line[len(_ORIGINAL_TRAILER) :].strip() or None
    return None


def _entry(trash_id: str, originals: list[str]) -> TrashEntry | None:
    if not originals:
        return None
    meta = wiki_git.last_commit_meta_for_path(f"{TRASH_DIR}/{trash_id}")
    _sha, author, ts, message = meta if meta else ("", "", "", "")
    root = _original_from_message(message)
    if root is None:
        # No trailer (trash predating trash_commit_message) — infer from the file
        # list. Ambiguous for a single-file folder, hence the trailer above.
        root = originals[0] if len(originals) == 1 else os.path.commonpath(originals)
    kind = PageKind.of(root)
    return TrashEntry(
        trash_id=trash_id,
        original_path=root,
        kind=kind,
        trashed_by=author,
        trashed_at=ts,
    )


def list_entries() -> list[TrashEntry]:
    """All trashed items, newest-first. Derived from the ``.trash/`` tree."""
    prefix = f"{TRASH_DIR}/"
    groups: dict[str, list[str]] = {}
    for f in wiki_git.list_trash_files():
        rest = f[len(prefix) :]  # "<trash_id>/<original/path>"
        trash_id, _, original = rest.partition("/")
        if original:
            groups.setdefault(trash_id, []).append(original)
    # Order by trash-move commit recency (newest first) — deterministic even
    # for two moves in the same author-date second, which a trashed_at-string
    # sort can't disambiguate. This makes "newest tombstone for a path" (which a
    # re-deleted page's id URL depends on) stable. Ids missing from the git
    # ordering (shouldn't happen) sort last, stably.
    rank = {tid: i for i, tid in enumerate(wiki_git.trash_ids_newest_first())}
    entries = [e for tid, orig in groups.items() if (e := _entry(tid, orig))]
    entries.sort(key=lambda e: rank.get(e.trash_id, len(rank)))
    return entries


def entry_for_original_path(path: str) -> TrashEntry | None:
    """The most-recently-trashed entry whose original location was ``path``,
    or ``None`` if that path was never trashed (or its trash was purged).

    Powers the deleted-URL tombstone: a page/folder deleted from ``path`` can
    have several tombstones (deleted, recreated, deleted again) — the newest
    is the one a Restore would bring back. ``list_entries`` is newest-first,
    so the first match wins."""
    return next((e for e in list_entries() if e.original_path == path), None)


def entry_for(trash_id: str) -> TrashEntry | None:
    """The trash entry for ``trash_id``, or ``None`` if unknown/empty."""
    prefix = f"{TRASH_DIR}/{trash_id}/"
    originals = [
        f[len(prefix) :] for f in wiki_git.list_trash_files() if f.startswith(prefix)
    ]
    return _entry(trash_id, originals)


def purge(trash_id: str, actor: str | None = None) -> bool:
    """Permanently remove a trashed item (past retention): drop the ACL/owner/
    policy rows that trashing parked at the trash location, then ``git rm`` its
    ``.trash/<trash_id>/`` subtree + commit. Content stays in git history (soft
    purge). Returns ``True`` if anything was purged.

    **Order matters — cleanup before `git rm`, so the purge is retryable.** The
    row deletes are idempotent; the `git rm` is the last, committing step. If any
    step fails, the `.trash/` files remain, so the next sweep re-runs the whole
    thing (re-clearing rows is a no-op) and finishes the `git rm`. Doing the
    `git rm` first would strand the parked rows on failure: the retry would
    short-circuit at the empty-`.trash/` guard below.

    Does *not* touch the item's ``wiki_doc_ids`` tombstone: the id keeps
    resolving to a deleted state, and the tombstone panel degrades to the
    generic "unavailable" card once ``/wiki/deleted`` 404s (the trash entry is
    gone). Retiring the id fully is a separate concern (the id→trash link isn't
    tracked, and multiple tombstones can share a path)."""
    prefix = f"{TRASH_DIR}/{trash_id}/"
    trash_paths = [f for f in wiki_git.list_trash_files() if f.startswith(prefix)]
    if not trash_paths:
        return False
    # Every `.trash/<id>/…` path that could hold a parked row: each file plus
    # its ancestor folders down to `.trash/<id>`.
    to_clear: set[str] = set()
    for p in trash_paths:
        parts = p.split("/")
        for i in range(2, len(parts) + 1):
            to_clear.add("/".join(parts[:i]))
    for path in to_clear:
        acl.delete_all_for_path(path)
        # delete_at (not delete): the parked key is a `.trash/…` path, which
        # delete()'s safe_rel_path normalization rejects.
        update_policy.delete_at(path)
    # git rm last — the committing step, after the idempotent row cleanup.
    wiki_git.purge_from_trash(trash_id, f"purge trash {trash_id}", author=actor)
    return True
