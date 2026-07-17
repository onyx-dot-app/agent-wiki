"""Shared skeleton for re-anchoring span records when a commit changes a page body.

Comments and ingest source ranges both anchor a ``[start_offset, end_offset)``
range to a page at an ``anchor_sha`` and re-derive it against later commits. This
holds the one non-trivial part they share: run synchronously from
``app.wiki.notify`` right after a write, batch the git reads by ``anchor_sha``
(a page's records almost always share the previous HEAD, so it reads the old
body once and the new body once), follow renames, and run the pure
``comment_anchor.remap_range`` diff per record. The per-type differences (which
records are stale, and what to do with a survivor vs a lost span) are the three
callbacks.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from app.wiki import comment_anchor
from app.wiki import git as wiki_git
from app.wiki.git import UnknownSha

log = logging.getLogger(__name__)

# (doc_path, head_sha) -> stale rows, each a dict with id/anchor_sha/start_offset/end_offset.
FetchStale = Callable[[str, str], list[dict[str, Any]]]
# (row_id, *, start_offset, end_offset, quoted_text, anchor_sha) -> None.
OnRemapped = Callable[..., None]
# (row_id) -> None.
OnLost = Callable[[Any], None]


def remap_anchored(
    path: str, *, fetch_stale: FetchStale, on_remapped: OnRemapped, on_lost: OnLost
) -> None:
    """Bring every stale anchored span on ``path`` up to the current HEAD."""
    if not path.endswith(".md"):
        return
    head = wiki_git.head_sha_for_path(path)
    if head is None:
        return  # untracked / no commits touch this path
    rows = fetch_stale(path, head)
    if not rows:
        return
    try:
        new_body = wiki_git.read_file(path, head)
    except UnknownSha:
        log.warning("remap_anchored: cannot read %s at HEAD %s", path, head)
        return

    by_anchor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_anchor[r["anchor_sha"]].append(r)

    for anchor_sha, group in by_anchor.items():
        old_body = _read_old_body(path, anchor_sha)
        if old_body is None:
            continue
        for r in group:
            result = comment_anchor.remap_range(
                old_body, new_body, r["start_offset"], r["end_offset"]
            )
            if result is None:
                on_lost(r["id"])
            else:
                start, end = result
                on_remapped(
                    r["id"],
                    start_offset=start,
                    end_offset=end,
                    quoted_text=new_body[start:end],
                    anchor_sha=head,
                )


def _read_old_body(path: str, anchor_sha: str) -> str | None:
    """Read the page body at ``anchor_sha``, following any rename. Returns
    ``None`` (caller skips the group) when the anchor commit is not reachable,
    leaving those records untouched to retry on the next commit."""
    old_path = wiki_git.path_at_ref(path, anchor_sha)
    if old_path is None:
        log.warning("remap_anchored: anchor %s not in history of %s", anchor_sha, path)
        return None
    try:
        return wiki_git.read_file(old_path, anchor_sha)
    except UnknownSha:
        log.warning("remap_anchored: cannot read %s at anchor %s", old_path, anchor_sha)
        return None
