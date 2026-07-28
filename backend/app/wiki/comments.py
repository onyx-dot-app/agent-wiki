"""Comments repo — SQLAlchemy ORM. Free functions over the ``Comment`` model.

Comments are **Postgres-only** (never written to the wiki git repo) and are
anchored to a page by ``doc_path`` + a character range; see
``app/db/models.py:Comment`` and ``app/wiki/comment_anchor.py`` for the anchor
model. Like every repo here, these functions open their own ``session()`` and
return plain dicts so callers don't depend on the ORM.

Threads: a top-level comment is a *root* (``thread_root_id == id``,
``parent_id is None``) and carries the anchor + resolve state. Replies share
the root's ``thread_root_id`` and leave the anchor columns NULL. ``resolve`` /
``reopen`` act on the root; ``remap`` / ``orphan`` are called by the
commit-time re-anchor task.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db import comment_fts
from app.db.models import Comment, User
from app.db.session import execute_dml, session
from app.models.comment import CommentAuthorKind, CommentScope, CommentStatus
from app.wiki import comment_anchor, comment_mentions
from app.wiki import git as wiki_git

log = logging.getLogger(__name__)


def _index_thread(thread_root_id: str) -> None:
    """Sync a thread to the comment search index after a change. Re-indexes
    every comment in the thread with the thread's status (taken from the root),
    or removes the thread from the index if its root is gone or orphaned.
    Best-effort: never raises, so an index glitch can't fail a mutation."""
    try:
        with session() as s:
            root = s.get(Comment, thread_root_id)
            if root is None or root.status == CommentStatus.ORPHANED.value:
                comment_fts.delete_thread(thread_root_id)
                return
            rows = s.scalars(
                select(Comment).where(Comment.thread_root_id == thread_root_id)
            ).all()
            if not rows:
                comment_fts.delete_thread(thread_root_id)
                return
            status = root.status
            docs = [
                comment_fts.CommentDoc(
                    comment_id=c.id,
                    doc_path=c.doc_path,
                    thread_root_id=c.thread_root_id,
                    body=comment_mentions.detokenize(c.body),
                    author_user_id=c.author_user_id,
                    mentioned_user_ids=comment_mentions.mentioned_ids(c.body),
                    status=status,
                )
                for c in rows
            ]
        for doc in docs:
            comment_fts.index_comment(doc)
    except Exception:
        log.warning("comment index: _index_thread failed for %s", thread_root_id, exc_info=True)

# Derived from the enums in app/models/comment.py so they stay the single
# source of truth (the DB CHECK constraints mirror the same values).
_VALID_SCOPES = frozenset(s.value for s in CommentScope)
_VALID_AUTHOR_KINDS = frozenset(k.value for k in CommentAuthorKind)
# Statuses a caller may set. 'orphaned' is system-only (set by the re-anchor
# path when a span collapses), so it is not accepted from the resolve path.
_SETTABLE_STATUSES = frozenset({CommentStatus.OPEN.value, CommentStatus.RESOLVED.value})


def _author_displays(s: Session, comments: Iterable[Comment]) -> dict[str, str]:
    """Map each comment author's user id to a display string (name, or email
    when unnamed), fetched for all distinct authors in one query."""
    ids = {c.author_user_id for c in comments if c.author_user_id}
    if not ids:
        return {}
    rows = s.execute(select(User.id, User.name, User.email).where(User.id.in_(ids))).all()
    return {uid: (name or email) for uid, name, email in rows}


def _to_dict(c: Comment, displays: dict[str, str]) -> dict[str, Any]:
    if c.author_user_id:
        author_display = displays.get(c.author_user_id)
    elif c.author_kind == CommentAuthorKind.AGENT.value:
        author_display = "Agent"
    else:
        author_display = None
    return {
        "id": c.id,
        "doc_path": c.doc_path,
        "thread_root_id": c.thread_root_id,
        "parent_id": c.parent_id,
        "scope": c.scope,
        "anchor_sha": c.anchor_sha,
        "start_offset": c.start_offset,
        "end_offset": c.end_offset,
        "quoted_text": c.quoted_text,
        "author_kind": c.author_kind,
        "author_user_id": c.author_user_id,
        "author_display": author_display,
        "body": c.body,
        "status": c.status,
        "resolved_by_user_id": c.resolved_by_user_id,
        "resolved_at": c.resolved_at,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


def _now_text(s: Any) -> str:
    """ISO timestamp matching the column's server_default."""
    return s.scalar(
        select(func.to_char(func.timezone("UTC", func.now()), "YYYY-MM-DD HH24:MI:SS"))
    )


# --------------------------------------------------------------------------- #
# Create                                                                      #
# --------------------------------------------------------------------------- #


def create_thread(
    *,
    doc_path: str,
    body: str,
    author_user_id: str | None,
    anchor_sha: str | None,
    start_offset: int | None,
    end_offset: int | None,
    quoted_text: str | None,
    scope: str = CommentScope.INLINE.value,
    author_kind: str = CommentAuthorKind.USER.value,
) -> dict[str, Any]:
    """Create a new root comment (starts a thread). Returns the row dict.

    For ``scope='inline'`` the anchor (``anchor_sha`` + both offsets) is
    required — the DB enforces this too, but we fail early with a clear error.
    """
    if scope not in _VALID_SCOPES:
        raise ValueError(f"invalid scope: {scope!r}")
    if author_kind not in _VALID_AUTHOR_KINDS:
        raise ValueError(f"invalid author_kind: {author_kind!r}")
    if scope == CommentScope.INLINE.value and (
        anchor_sha is None or start_offset is None or end_offset is None
    ):
        raise ValueError("inline comment requires anchor_sha + start/end offset")

    if (
        scope == CommentScope.INLINE.value
        and anchor_sha is not None
        and start_offset is not None
        and end_offset is not None
    ):
        # The frontend's span is only an approximation (textOffsets.ts strips
        # markdown syntax when computing it) — correct it against the real
        # markdown source before it's ever persisted. Uncorrected, it drifts
        # further from the true span with every future remap_range call
        # (an exact character diff, no fuzzy matching of its own).
        #
        # Best-effort: a git read failure here (e.g. WIKI_DIR not set up) must
        # never block comment creation over a correction that's a nicety, not
        # a requirement — fall back to the frontend's own approximate span,
        # same as if quoted_text simply weren't found in the body.
        try:
            page_body = wiki_git.read_file_opt(doc_path, anchor_sha)
        except OSError:
            log.warning("create_thread: could not read %s@%s to correct span", doc_path, anchor_sha)
            page_body = None
        if page_body is not None:
            start_offset, end_offset = comment_anchor.resolve_exact_span(
                page_body, start_offset, end_offset, quoted_text or ""
            )

    cid = f"cmt_{uuid.uuid4().hex[:12]}"
    with session() as s:
        now = _now_text(s)
        c = Comment(
            id=cid,
            doc_path=doc_path,
            thread_root_id=cid,
            parent_id=None,
            scope=scope,
            anchor_sha=anchor_sha,
            start_offset=start_offset,
            end_offset=end_offset,
            quoted_text=quoted_text,
            author_kind=author_kind,
            author_user_id=author_user_id,
            body=body,
            status=CommentStatus.OPEN.value,
            created_at=now,
            updated_at=now,
        )
        s.add(c)
        s.flush()
        result = _to_dict(c, _author_displays(s, [c]))
    _index_thread(cid)
    log.info("comment thread created id=%s doc=%s", cid, doc_path)
    return result


def add_reply(
    *,
    parent_id: str,
    body: str,
    author_user_id: str | None,
    author_kind: str = CommentAuthorKind.USER.value,
) -> dict[str, Any] | None:
    """Reply to an existing comment. ``thread_root_id`` and ``doc_path`` are
    inherited from the parent; the anchor columns stay NULL (replies inherit
    the thread's position). Returns the row dict, or ``None`` if the parent
    doesn't exist."""
    if author_kind not in _VALID_AUTHOR_KINDS:
        raise ValueError(f"invalid author_kind: {author_kind!r}")
    cid = f"cmt_{uuid.uuid4().hex[:12]}"
    with session() as s:
        parent = s.get(Comment, parent_id)
        if parent is None:
            return None
        now = _now_text(s)
        c = Comment(
            id=cid,
            doc_path=parent.doc_path,
            thread_root_id=parent.thread_root_id,
            parent_id=parent_id,
            scope=parent.scope,
            anchor_sha=None,
            start_offset=None,
            end_offset=None,
            quoted_text=None,
            author_kind=author_kind,
            author_user_id=author_user_id,
            body=body,
            status=CommentStatus.OPEN.value,
            created_at=now,
            updated_at=now,
        )
        s.add(c)
        s.flush()
        result = _to_dict(c, _author_displays(s, [c]))
        thread_root_id = parent.thread_root_id
    _index_thread(thread_root_id)
    return result


# --------------------------------------------------------------------------- #
# Read                                                                        #
# --------------------------------------------------------------------------- #


def get(comment_id: str) -> dict[str, Any] | None:
    with session() as s:
        c = s.get(Comment, comment_id)
        return _to_dict(c, _author_displays(s, [c])) if c else None


def list_for_doc(doc_path: str) -> list[dict[str, Any]]:
    """All comments on a page (roots + replies), oldest first. The API layer
    groups these into threads by ``thread_root_id`` and decides what to show
    (e.g. hiding resolved threads)."""
    with session() as s:
        rows = s.scalars(
            select(Comment)
            .where(Comment.doc_path == doc_path)
            .order_by(Comment.created_at.asc())
        ).all()
        displays = _author_displays(s, rows)
        return [_to_dict(c, displays) for c in rows]


def list_thread(thread_root_id: str) -> list[dict[str, Any]]:
    with session() as s:
        rows = s.scalars(
            select(Comment)
            .where(Comment.thread_root_id == thread_root_id)
            .order_by(Comment.created_at.asc())
        ).all()
        displays = _author_displays(s, rows)
        return [_to_dict(c, displays) for c in rows]


# --------------------------------------------------------------------------- #
# Mutate — user-driven                                                        #
# --------------------------------------------------------------------------- #


def edit_body(comment_id: str, body: str) -> dict[str, Any] | None:
    with session() as s:
        c = s.get(Comment, comment_id)
        if c is None:
            return None
        c.body = body
        c.updated_at = _now_text(s)
        s.flush()
        result = _to_dict(c, _author_displays(s, [c]))
        thread_root_id = c.thread_root_id
    _index_thread(thread_root_id)
    return result


def set_thread_status(
    thread_root_id: str,
    status: str,
    *,
    resolved_by_user_id: str | None = None,
) -> dict[str, Any] | None:
    """Resolve (``'resolved'``) or reopen (``'open'``) a thread. Acts on the
    root row; replies don't carry meaningful status. Returns the updated root
    dict, or ``None`` if the root doesn't exist."""
    if status not in _SETTABLE_STATUSES:
        raise ValueError(f"status must be one of {_SETTABLE_STATUSES}, got {status!r}")
    with session() as s:
        root = s.get(Comment, thread_root_id)
        if root is None:
            return None
        now = _now_text(s)
        root.status = status
        root.updated_at = now
        if status == CommentStatus.RESOLVED.value:
            root.resolved_by_user_id = resolved_by_user_id
            root.resolved_at = now
        else:
            root.resolved_by_user_id = None
            root.resolved_at = None
        s.flush()
        result = _to_dict(root, _author_displays(s, [root]))
    _index_thread(thread_root_id)
    return result


def delete(comment_id: str) -> bool:
    """Delete a comment. Returns True if a row was deleted.

    - **Root** (``parent_id IS NULL``): deletes the whole thread — every reply
      cascades via the ``parent_id`` ON DELETE CASCADE.
    - **Non-root**: deletes only this comment; its direct replies are *kept* by
      first re-parenting them up to this comment's parent, so a mid-thread
      delete never takes the replies-to-it down with it.
    """
    with session() as s:
        c = s.get(Comment, comment_id)
        if c is None:
            return False
        is_root = c.parent_id is None
        thread_root_id = c.thread_root_id
        if not is_root:
            # Promote this comment's children to its parent, then delete only
            # this node (nothing references it anymore, so the cascade is a
            # no-op beyond the single row).
            s.execute(
                update(Comment)
                .where(Comment.parent_id == comment_id)
                .values(parent_id=c.parent_id)
                .execution_options(synchronize_session=False)
            )
        s.delete(c)
    if is_root:
        # Root delete cascades the whole thread → drop all its index docs.
        comment_fts.delete_thread(thread_root_id)
    else:
        comment_fts.delete_comment(comment_id)
    return True


# --------------------------------------------------------------------------- #
# Mutate — re-anchor task (commit-time drift)                                 #
# --------------------------------------------------------------------------- #


def roots_needing_remap(doc_path: str, head_sha: str) -> list[dict[str, Any]]:
    """Inline thread roots on ``doc_path`` whose stored anchor is not yet at
    ``head_sha`` and should track edits — i.e. **open** threads only. The
    re-anchor task diffs each one's body-at-``anchor_sha`` to the body at HEAD.

    Orphaned and resolved threads are deliberately excluded. Orphaned ones have
    no live span left; resolved ones are done, so re-anchoring a resolved
    comment (and re-orphaning it when its span is gone) would silently flip it
    back out of ``resolved`` — which read as "resolve doesn't stick". A reopened
    thread returns to ``open`` and re-enters this pool, so its anchor catches up
    then."""
    with session() as s:
        rows = s.scalars(
            select(Comment).where(
                Comment.doc_path == doc_path,
                Comment.parent_id.is_(None),
                Comment.scope == CommentScope.INLINE.value,
                Comment.status == CommentStatus.OPEN.value,
                Comment.anchor_sha != head_sha,
            )
        ).all()
        displays = _author_displays(s, rows)
        return [_to_dict(c, displays) for c in rows]


def apply_remap(
    comment_id: str,
    *,
    start_offset: int,
    end_offset: int,
    quoted_text: str,
    anchor_sha: str,
) -> None:
    """Advance a comment's anchor to a new commit after a successful remap.

    Refreshes the offsets + ``quoted_text`` to the new body and sets
    ``anchor_sha`` to the version they now describe. Does **not** touch
    ``updated_at`` — a re-anchor is system bookkeeping, not user activity."""
    with session() as s:
        c = s.get(Comment, comment_id)
        if c is None:
            return
        c.start_offset = start_offset
        c.end_offset = end_offset
        c.quoted_text = quoted_text
        c.anchor_sha = anchor_sha


def orphan(comment_id: str) -> None:
    """Mark a comment orphaned (its anchored span collapsed). Offsets,
    ``anchor_sha`` and ``quoted_text`` are left untouched so they freeze as the
    tombstone of the last version the comment was anchored to."""
    with session() as s:
        c = s.get(Comment, comment_id)
        if c is None:
            return
        c.status = CommentStatus.ORPHANED.value
        thread_root_id = c.thread_root_id
    # Orphaned threads drop out of search (their span is gone).
    _index_thread(thread_root_id)


def reassign_doc_path(old_path: str, new_path: str) -> int:
    """Re-key all comments from ``old_path`` to ``new_path`` (page move/rename).
    Returns the number of rows moved."""
    with session() as s:
        moved = execute_dml(
            s,
            update(Comment)
            .where(Comment.doc_path == old_path)
            .values(doc_path=new_path)
            .execution_options(synchronize_session=False),
        )
    comment_fts.reassign_doc_path(old_path, new_path)
    return moved


def orphan_all_for_doc(doc_path: str) -> int:
    """Orphan every still-anchored comment on a page (page deleted). Returns
    the number of rows orphaned."""
    with session() as s:
        orphaned = execute_dml(
            s,
            update(Comment)
            .where(Comment.doc_path == doc_path, Comment.status != CommentStatus.ORPHANED.value)
            .values(status=CommentStatus.ORPHANED.value)
            .execution_options(synchronize_session=False),
        )
    # Page deleted → its comments become tombstones in Postgres but leave search.
    comment_fts.delete_for_doc(doc_path)
    return orphaned


def reindex_all_inline() -> None:
    """Index every comment thread into the search index. Called at startup so
    comments created before the feature (or while the index was down) become
    searchable. Idempotent — re-indexing a thread is an upsert."""
    with session() as s:
        root_ids = s.scalars(
            select(Comment.id).where(Comment.parent_id.is_(None))
        ).all()
    if not root_ids:
        return
    log.info("comment index: backfilling %d thread(s) at startup", len(root_ids))
    for rid in root_ids:
        _index_thread(rid)
