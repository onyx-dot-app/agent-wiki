"""Re-anchor ingest source ranges when a commit changes the page body.

A thin binding of the shared ``anchor_remap`` skeleton to the provenance repo. A
range whose span survives is advanced to the new HEAD, one whose span was
rewritten is retired. The caller in ``notify`` swallows failures so a remap
hiccup can never break a save.
"""
from __future__ import annotations

from app.db import provenance as db_provenance
from app.wiki.anchor_remap import remap_anchored


def remap_source_ranges(path: str) -> None:
    """Bring every live source range on ``path`` up to the current HEAD."""
    remap_anchored(
        path,
        fetch_stale=db_provenance.live_ranges_needing_remap,
        on_remapped=db_provenance.apply_range_remap,
        on_lost=db_provenance.retire_range,
    )
