"""Enumerations for wiki page comments.

``str, Enum`` so members compare/serialize as their string value (matching the
``ChangeKind`` pattern in ``app/models/wiki.py``). These are the single source
of truth for the valid column values; the DB CHECK constraints in
``app/db/models.py`` mirror them, the repo validates against them, and the HTTP
layer will type its request/response fields with them.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.models.coedit import LiveAnchor


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


# --------------------------------------------------------------------------- #
# HTTP shapes                                                                 #
# --------------------------------------------------------------------------- #


class CreateCommentRequest(BaseModel):
    """Start an inline comment thread on a page.

    ``anchor_sha`` + the offsets are the version the client *read* and computed
    the selection against — not necessarily current HEAD. The server stores
    them as-is; the re-anchor path drifts them to HEAD. (This is what makes a
    comment created against a stale view land correctly.)
    """

    path: str = Field(min_length=1)
    anchor_sha: str = Field(min_length=1)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    quoted_text: str = Field(min_length=1)
    body: str = Field(min_length=1)


class CreateReplyRequest(BaseModel):
    body: str = Field(min_length=1)


class EditCommentRequest(BaseModel):
    body: str = Field(min_length=1)


class CommentView(BaseModel):
    id: str
    doc_path: str
    thread_root_id: str
    parent_id: str | None
    scope: CommentScope
    anchor_sha: str | None
    start_offset: int | None
    end_offset: int | None
    quoted_text: str | None
    author_kind: CommentAuthorKind
    author_user_id: str | None
    # Human label for the author (user's name/email, or "Agent"); None when the
    # author can't be resolved. Display-only — authorization still uses the id.
    author_display: str | None = None
    body: str
    status: CommentStatus
    resolved_by_user_id: str | None
    resolved_at: str | None
    created_at: str
    updated_at: str
    # Set only when the page has an active live co-edit session and the
    # anchor (already current as of HEAD via app/wiki/anchor_remap.py)
    # could be re-resolved against that session's live, uncommitted doc —
    # see app/wiki/coedit_ws.py:resolve_live_spans. None when there's no
    # live session, the comment is PAGE-scoped, or the live-doc resolution
    # itself orphaned (the frontend then falls back to not highlighting
    # this comment in the editor, same as any other orphaned anchor).
    live_start: LiveAnchor | None = None
    live_end: LiveAnchor | None = None


class CommentThreadView(BaseModel):
    """A root comment plus its replies (oldest first)."""

    root: CommentView
    replies: list[CommentView]


class CommentListResponse(BaseModel):
    threads: list[CommentThreadView]


class CommentSearchHit(BaseModel):
    """A comment matched by full-text search. ``doc_path`` + ``thread_root_id``
    let the UI deep-link to the thread via ``/app/wiki/<path>?comment=<id>``.
    ``snippet`` is plain text (highlight tags stripped) — render as text."""

    comment_id: str
    doc_path: str
    thread_root_id: str
    snippet: str
    score: float


class CommentSearchResponse(BaseModel):
    query: str
    hits: list[CommentSearchHit]
