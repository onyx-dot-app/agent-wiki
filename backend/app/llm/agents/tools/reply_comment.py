"""Handler for the `reply_comment` tool. Spec lives in `reply_comment.json`.

Lets an agent reply in an existing comment thread (agent-authored discussion).
No anchoring: a reply inherits the thread's anchor and `doc_path` from its
parent, so the only inputs are the `comment_id` to reply under (from a
`search_comments` hit) and the `body`.

Attributed to the user driving the chat (`author_user_id = current_user()`)
with `author_kind="agent"` provenance — same model as `add_comment`. Permission
mirrors human replying: read access to the parent's page, via
`require_can("read", parent.doc_path)`.
"""
from __future__ import annotations

from typing import Any

from app.auth import PermissionDenied, current_user, require_can
from app.llm.agents.tools._links import thread_link
from app.models.comment import CommentAuthorKind
from app.wiki import comments as comments_repo


def handle(args: dict[str, Any]) -> Any:
    comment_id = args.get("comment_id")
    if not isinstance(comment_id, str) or not comment_id.strip():
        return {"error": "comment_id is required — the id of a comment in the thread to reply to"}
    comment_id = comment_id.strip()

    body = args.get("body")
    if not isinstance(body, str) or not body.strip():
        return {"error": "body is required"}

    parent = comments_repo.get(comment_id)
    if parent is None:
        return {"error": f"comment not found: {comment_id}"}

    try:
        require_can("read", parent["doc_path"])
    except PermissionDenied as exc:
        return {"error": str(exc)}

    user = current_user()
    reply = comments_repo.add_reply(
        parent_id=comment_id,
        body=body.strip(),
        author_user_id=user.id if user else None,
        author_kind=CommentAuthorKind.AGENT.value,
    )
    if reply is None:  # parent vanished between get() and add_reply()
        return {"error": f"comment not found: {comment_id}"}
    return {
        "comment_id": reply["id"],
        "thread_root_id": reply["thread_root_id"],
        "doc_path": reply["doc_path"],
        "link": thread_link(reply["doc_path"], reply["thread_root_id"]),
    }
