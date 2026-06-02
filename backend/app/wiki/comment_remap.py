"""Re-anchor wiki page comments when a commit changes the page body.

Runs **synchronously** from ``app.wiki.notify`` right after a write commits, so
a comment's stored offsets are correct as soon as the save returns — no
eventual-consistency window. This applies uniformly to human saves, API edits,
and agent rewrites, since all of them land as commits through ``notify``.

It's cheap to do inline because the git reads are **batched by anchor_sha**: in
steady state every comment on a page shares the same anchor (the previous
HEAD), so we read the old body once and the new body once regardless of how
many comments the page has, then run an in-memory ``difflib`` diff per comment
(see ``app/wiki/comment_anchor.py``). Two git reads plus pure-CPU diffs sit well
within a request budget.

The caller in ``notify`` swallows failures so a remap hiccup can never break a
save, and the API read path remaps inline if it ever sees a stale anchor — so
correctness never depends on this having run.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from app.wiki import comment_anchor, comments
from app.wiki import git as wiki_git
from app.wiki.git import UnknownSha

log = logging.getLogger(__name__)


def remap_comments(path: str) -> None:
    """Bring every stale comment anchor on ``path`` up to the current HEAD."""
    if not path.endswith(".md"):
        return
    head = wiki_git.head_sha_for_path(path)
    if head is None:
        return  # untracked / no commits touch this path
    roots = comments.roots_needing_remap(path, head)
    if not roots:
        return
    try:
        new_body = wiki_git.read_file(path, head)
    except UnknownSha:
        log.warning("remap_comments: cannot read %s at HEAD %s", path, head)
        return

    # Group by the commit each comment is anchored at: comments on a page
    # almost always share one anchor_sha (the previous HEAD), so this reads
    # each old body once rather than once per comment.
    by_anchor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in roots:
        by_anchor[c["anchor_sha"]].append(c)

    for anchor_sha, group in by_anchor.items():
        old_body = _read_old_body(path, anchor_sha)
        if old_body is None:
            continue
        for c in group:
            _apply(c, old_body, new_body, head)


def _read_old_body(path: str, anchor_sha: str) -> str | None:
    """Read the page body at ``anchor_sha``, following any rename so
    ``git show <sha>:<path>`` resolves. Returns ``None`` (caller skips the
    group) when the anchor commit isn't reachable — better to leave those
    comments untouched and retry on the next commit than to guess."""
    old_path = wiki_git.path_at_ref(path, anchor_sha)
    if old_path is None:
        log.warning("remap_comments: anchor %s not in history of %s", anchor_sha, path)
        return None
    try:
        return wiki_git.read_file(old_path, anchor_sha)
    except UnknownSha:
        log.warning("remap_comments: cannot read %s at anchor %s", old_path, anchor_sha)
        return None


def _apply(c: dict[str, Any], old_body: str, new_body: str, head: str) -> None:
    result = comment_anchor.remap_range(
        old_body, new_body, c["start_offset"], c["end_offset"]
    )
    if result is None:
        comments.orphan(c["id"])
    else:
        start, end = result
        comments.apply_remap(
            c["id"],
            start_offset=start,
            end_offset=end,
            quoted_text=new_body[start:end],
            anchor_sha=head,
        )
