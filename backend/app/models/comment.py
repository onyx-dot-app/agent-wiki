"""Enumerations for wiki page comments.

``str, Enum`` so members compare/serialize as their string value (matching the
``ChangeKind`` pattern in ``app/models/wiki.py``). These are the single source
of truth for the valid column values; the DB CHECK constraints in
``app/db/models.py`` mirror them, the repo validates against them, and the HTTP
layer will type its request/response fields with them.
"""
from __future__ import annotations

from enum import Enum


class CommentScope(str, Enum):
    """What a comment is attached to."""

    INLINE = "inline"  # anchored to a character range in the body
    PAGE = "page"  # whole-page (footer) thread


class CommentAuthorKind(str, Enum):
    """Who wrote a comment."""

    USER = "user"
    AGENT = "agent"


class CommentStatus(str, Enum):
    """Lifecycle state of a comment thread.

    ``ORPHANED`` is set only by the re-anchor path when a span collapses — it is
    never a status a caller may set directly (see the repo's resolve path).
    """

    OPEN = "open"
    RESOLVED = "resolved"
    ORPHANED = "orphaned"
