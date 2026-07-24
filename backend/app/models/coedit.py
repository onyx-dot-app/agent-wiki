"""Shared wire shape for resolving a comment/source anchor against a page's
live (uncommitted) co-edit doc. See ``app/wiki/coedit_ws.py:resolve_live_spans``.
"""

from __future__ import annotations

from pydantic import BaseModel


class LiveAnchor(BaseModel):
    """A resolved position in a session's live doc: the top-level block
    carrying it (``markdown_yjs.BLOCK_ID_ATTR``) and the character offset
    within that block's own reserialized text. Block-relative rather than a
    flat document offset because the block id is the only thing guaranteed
    stable across the live doc and the checkpoint/HEAD text a comment or
    source span was originally anchored against — see
    ``Engineering Projects/Agent Wiki Project/design/Co-Editing.md``."""

    block_id: str
    block_offset: int
