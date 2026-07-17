"""Re-anchor wiki page comments when a commit changes the page body.

A thin binding of the shared ``anchor_remap`` skeleton to the comments repo. The
caller in ``notify`` swallows failures so a remap hiccup can never break a save,
and the API read path remaps inline if it ever sees a stale anchor, so
correctness never depends on this having run.
"""
from __future__ import annotations

from app.wiki import comments
from app.wiki.anchor_remap import remap_anchored


def remap_comments(path: str) -> None:
    """Bring every stale comment anchor on ``path`` up to the current HEAD."""
    remap_anchored(
        path,
        fetch_stale=comments.roots_needing_remap,
        on_remapped=comments.apply_remap,
        on_lost=comments.orphan,
    )
