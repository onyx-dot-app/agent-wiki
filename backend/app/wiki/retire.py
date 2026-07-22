"""Retire a page into a surviving page — the identity-forwarding half of a merge.

Retiring is what distinguishes "these two paths are the same document,
consolidate them" from a plain delete: the source page is trash-moved (so the
retire stays losslessly restorable, like every delete), but its *identity*
survives — the stable doc id forwards to the surviving page, so old links and
bookmarks resolve to the survivor instead of a tombstone, and its comment
threads re-anchor to the survivor instead of riding into the trash.

Content is out of scope here: whoever calls this has already decided what the
surviving page's body should be (and written it, if it changed). This module
only retires the redundant path and forwards its references.
"""
from __future__ import annotations

import logging

from app.models.wiki import PathMove
from app.wiki import comments, doc_ids, git, notify, trash
from app.wiki.filesystem import safe_rel_path

log = logging.getLogger(__name__)


def retire_page(source: str, target: str, *, author: str | None = None) -> str:
    """Trash-move ``source`` and forward its identity to ``target``.

    Both must be tracked ``.md`` pages. Returns the commit SHA of the
    trash-move. Side effects beyond the standard trash lifecycle
    (``after_doc_trashed``: ACL/policy re-point to trash, search/live drop):

    - the source's doc id forwards to the target's id — ``doc_ids.resolve``
      (and the id-URL endpoint) lands on the survivor;
    - the source's comment threads re-anchor to the target path.

    Restoring the source from Trash undoes the forward (the id re-binds to
    the restored page); re-anchored comments deliberately stay with the
    survivor — they were conversations about content that now lives there.
    """
    src = safe_rel_path(source)
    tgt = safe_rel_path(target)
    if src == tgt:
        raise ValueError("source and target are the same page")
    if not src.endswith(".md") or not tgt.endswith(".md"):
        raise ValueError("retire_page operates on .md pages")
    tracked = set(git.list_paths())
    if src not in tracked:
        raise ValueError(f"source page not found: {src!r}")
    if tgt not in tracked:
        raise ValueError(f"target page not found: {tgt!r}")

    # Capture both ids while the source is still live — the trash-move
    # tombstones its row, and a tombstone can't be minted for.
    source_id = doc_ids.get_or_mint(src)
    target_id = doc_ids.get_or_mint(tgt)

    dest = trash.trash_location(trash.new_trash_id(), src)
    sha, moves = git.move_path(
        src, dest, f"Retire {src} into {tgt}", author=author
    )
    notify.after_doc_trashed(
        moves, sha, author, root_move=PathMove(old=src, new=dest)
    )
    # The standard trash lifecycle carried the comments to the trash path and
    # tombstoned the id; the retire deltas redirect both at the survivor.
    moved = comments.reassign_doc_path(dest, tgt)
    doc_ids.set_forward(source_id, target_id)
    log.info(
        "retire: %s -> %s (sha=%s, comments re-anchored=%d)",
        src,
        tgt,
        sha[:8],
        moved,
    )
    return sha
