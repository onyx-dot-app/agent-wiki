"""Handler for the `resolve_comment` tool. Spec lives in `resolve_comment.json`.

Lets an agent mark a comment thread resolved. Resolving dismisses a thread as
"handled", so this is the most delicate of the comment-write tools — the spec
and the `comments` skill steer the agent to only resolve when the user
*explicitly* asks, never on its own judgment. It's reversible (a human can
reopen from the panel), which is the backstop.

Operates on the whole thread: given any `comment_id` in the thread we resolve
its root (`set_thread_status`). Permission mirrors human resolving — read access
to the page (Google-Docs style, any reader can resolve).
"""
from __future__ import annotations

from typing import Any

from app.auth import PermissionDenied, current_user, require_can
from app.models.comment import CommentStatus
from app.wiki import comments as comments_repo


def handle(args: dict[str, Any]) -> Any:
    comment_id = args.get("comment_id")
    if not isinstance(comment_id, str) or not comment_id.strip():
        return {"error": "comment_id is required — a comment in the thread to resolve"}
    comment_id = comment_id.strip()

    comment = comments_repo.get(comment_id)
    if comment is None:
        return {"error": f"comment not found: {comment_id}"}

    try:
        require_can("read", comment["doc_path"])
    except PermissionDenied as exc:
        return {"error": str(exc)}

    user = current_user()
    root = comments_repo.set_thread_status(
        comment["thread_root_id"],
        CommentStatus.RESOLVED.value,
        resolved_by_user_id=user.id if user else None,
    )
    if root is None:
        return {"error": f"thread not found: {comment['thread_root_id']}"}
    return {
        "thread_root_id": root["id"],
        "doc_path": root["doc_path"],
        "status": root["status"],
    }
