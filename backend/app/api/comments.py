"""Comments API — thin HTTP layer over ``app.wiki.comments``.

Inline comments on wiki pages: list (grouped into threads), create, reply,
edit, resolve/reopen, delete. Every route gates on ``require_can("read", path,
user)`` — read access to a page is enough to comment (feedback, not mutation);
edit/delete additionally require the author (or an admin).

Business logic lives in the repo + re-anchor modules; this layer parses, gates,
serializes, and fires the activity event.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.auth import User, require_can
from app.auth.deps import require_user
from app.db import comment_fts
from app.db.models import Event
from app.db.session import session
from app.models.comment import (
    CommentListResponse,
    CommentSearchHit,
    CommentSearchResponse,
    CommentStatus,
    CommentThreadView,
    CommentView,
    CreateCommentRequest,
    CreateReplyRequest,
    EditCommentRequest,
)
from app.wiki import comment_remap, comments as comments_repo, filesystem
from app.wiki import comment_notifications

log = logging.getLogger(__name__)

router = APIRouter()


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _safe_path(path: str) -> str:
    if not path:
        raise HTTPException(status_code=400, detail="path required")
    try:
        return filesystem.safe_rel_path(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _load(comment_id: str) -> dict[str, Any]:
    c = comments_repo.get(comment_id)
    if c is None:
        raise HTTPException(status_code=404, detail="comment not found")
    return c


def _require_author_or_admin(comment: dict[str, Any], user: User) -> None:
    if not (user.is_admin or comment["author_user_id"] == user.id):
        raise HTTPException(
            status_code=403, detail="only the author can modify this comment"
        )


def _group_threads(rows: list[dict[str, Any]]) -> list[CommentThreadView]:
    """Group flat rows into threads (root + replies), oldest thread first."""
    by_thread: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_thread[r["thread_root_id"]].append(r)
    threads: list[CommentThreadView] = []
    for members in by_thread.values():
        root = next((m for m in members if m["id"] == m["thread_root_id"]), None)
        if root is None:
            continue  # defensive: replies with no surviving root
        replies = sorted(
            (m for m in members if m["id"] != root["id"]),
            key=lambda m: m["created_at"],
        )
        threads.append(
            CommentThreadView(
                root=CommentView.model_validate(root),
                replies=[CommentView.model_validate(m) for m in replies],
            )
        )
    threads.sort(key=lambda t: t.root.created_at)
    return threads


def _fire_event(doc_path: str, row: dict[str, Any], user: User) -> None:
    """Best-effort activity-feed event. The comment is already committed by the
    time this runs, so a failure here must not 500 the request (which could
    prompt a retry and a duplicate comment) — log and move on."""
    try:
        with session() as s:
            s.add(
                Event(
                    kind="page.comment",
                    actor=user.id,
                    target=doc_path,
                    payload_json=json.dumps(
                        {
                            "comment_id": row["id"],
                            "thread_root_id": row["thread_root_id"],
                            "doc_path": doc_path,
                        }
                    ),
                )
            )
    except Exception:
        log.exception("failed to record page.comment event for %s", doc_path)


# --------------------------------------------------------------------------- #
# Routes                                                                      #
# --------------------------------------------------------------------------- #


@router.get("", response_model=CommentListResponse)
def list_comments(
    path: str = "", user: User = Depends(require_user)
) -> CommentListResponse:
    rel_path = _safe_path(path)
    require_can("read", rel_path, user)
    # Backstop: re-anchor any comment whose anchor lags HEAD (idempotent — a
    # no-op once the on-commit remap has run, which is the normal case).
    try:
        comment_remap.remap_comments(rel_path)
    except Exception:
        log.exception("comment remap backstop failed for %s", rel_path)
    return CommentListResponse(threads=_group_threads(comments_repo.list_for_doc(rel_path)))


@router.get("/search", response_model=CommentSearchResponse)
def search_comments(
    user: User = Depends(require_user),
    q: str = "",
    limit: int = Query(10, ge=1, le=50),
) -> CommentSearchResponse:
    """Full-text search across comment bodies. Cross-page; results are filtered
    to pages the caller can read (comments inherit page read access). A hit
    carries ``doc_path`` + ``thread_root_id`` so the UI can deep-link to the
    thread via ``/app/wiki/<path>?comment=<id>``."""
    query = q.strip()
    if not query:
        return CommentSearchResponse(query="", hits=[])
    hits = comment_fts.search(
        query, limit=limit, user_id=user.id, is_admin=user.is_admin
    )
    return CommentSearchResponse(
        query=query,
        hits=[
            CommentSearchHit(
                comment_id=h.comment_id,
                doc_path=h.doc_path,
                thread_root_id=h.thread_root_id,
                snippet=h.snippet,
                score=h.score,
            )
            for h in hits
        ],
    )


@router.post("", response_model=CommentView, status_code=status.HTTP_201_CREATED)
def create_comment(
    req: CreateCommentRequest, user: User = Depends(require_user)
) -> CommentView:
    rel_path = _safe_path(req.path)
    require_can("read", rel_path, user)
    if req.end_offset <= req.start_offset:
        raise HTTPException(status_code=400, detail="end_offset must be > start_offset")
    row = comments_repo.create_thread(
        doc_path=rel_path,
        body=req.body,
        author_user_id=user.id,
        anchor_sha=req.anchor_sha,
        start_offset=req.start_offset,
        end_offset=req.end_offset,
        quoted_text=req.quoted_text,
    )
    _fire_event(rel_path, row, user)
    comment_notifications.queue_for_comment(row, author_id=user.id)
    return CommentView.model_validate(row)


@router.post(
    "/{comment_id}/replies",
    response_model=CommentView,
    status_code=status.HTTP_201_CREATED,
)
def reply_to_comment(
    comment_id: str, req: CreateReplyRequest, user: User = Depends(require_user)
) -> CommentView:
    parent = _load(comment_id)
    require_can("read", parent["doc_path"], user)
    row = comments_repo.add_reply(
        parent_id=comment_id, body=req.body, author_user_id=user.id
    )
    if row is None:  # parent deleted between load and insert
        raise HTTPException(status_code=404, detail="comment not found")
    comment_notifications.queue_for_comment(row, author_id=user.id)
    return CommentView.model_validate(row)


@router.patch("/{comment_id}", response_model=CommentView)
def edit_comment(
    comment_id: str, req: EditCommentRequest, user: User = Depends(require_user)
) -> CommentView:
    c = _load(comment_id)
    require_can("read", c["doc_path"], user)
    _require_author_or_admin(c, user)
    row = comments_repo.edit_body(comment_id, req.body)
    if row is None:
        raise HTTPException(status_code=404, detail="comment not found")
    return CommentView.model_validate(row)


@router.post("/{comment_id}/resolve", response_model=CommentView)
def resolve_thread(
    comment_id: str, user: User = Depends(require_user)
) -> CommentView:
    c = _load(comment_id)
    require_can("read", c["doc_path"], user)
    row = comments_repo.set_thread_status(
        c["thread_root_id"], CommentStatus.RESOLVED.value, resolved_by_user_id=user.id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="comment not found")
    return CommentView.model_validate(row)


@router.post("/{comment_id}/reopen", response_model=CommentView)
def reopen_thread(comment_id: str, user: User = Depends(require_user)) -> CommentView:
    c = _load(comment_id)
    require_can("read", c["doc_path"], user)
    row = comments_repo.set_thread_status(c["thread_root_id"], CommentStatus.OPEN.value)
    if row is None:
        raise HTTPException(status_code=404, detail="comment not found")
    return CommentView.model_validate(row)


@router.delete("/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_comment(comment_id: str, user: User = Depends(require_user)) -> Response:
    c = _load(comment_id)
    require_can("read", c["doc_path"], user)
    _require_author_or_admin(c, user)
    comments_repo.delete(comment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
