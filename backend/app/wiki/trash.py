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

from app.wiki import git as wiki_git
from app.wiki.filesystem import TRASH_DIR


class TrashEntry(BaseModel):
    """One trashed item (a page, or a folder root) for the Trash view."""

    trash_id: str
    original_path: str  # where it lived; restore moves it back here
    kind: str  # "page" | "folder"
    trashed_by: str  # git author of the trash-move commit
    trashed_at: str  # ISO-8601


def new_trash_id() -> str:
    return uuid.uuid4().hex[:12]


def trash_location(trash_id: str, original_path: str) -> str:
    """The ``.trash/`` path an item at ``original_path`` moves to when trashed."""
    return f"{TRASH_DIR}/{trash_id}/{original_path}"


def _entry(trash_id: str, originals: list[str]) -> TrashEntry | None:
    if not originals:
        return None
    root = originals[0] if len(originals) == 1 else os.path.commonpath(originals)
    kind = "page" if (len(originals) == 1 and root.endswith(".md")) else "folder"
    meta = wiki_git.last_commit_meta_for_path(f"{TRASH_DIR}/{trash_id}")
    _sha, author, ts = meta if meta else ("", "", "")
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
    entries = [e for tid, orig in groups.items() if (e := _entry(tid, orig))]
    entries.sort(key=lambda e: e.trashed_at, reverse=True)
    return entries


def entry_for(trash_id: str) -> TrashEntry | None:
    """The trash entry for ``trash_id``, or ``None`` if unknown/empty."""
    prefix = f"{TRASH_DIR}/{trash_id}/"
    originals = [
        f[len(prefix) :] for f in wiki_git.list_trash_files() if f.startswith(prefix)
    ]
    return _entry(trash_id, originals)
