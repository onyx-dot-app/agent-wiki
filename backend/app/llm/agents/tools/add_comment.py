"""Handler for the `add_comment` tool. Spec lives in `add_comment.json`.

Lets an agent leave an **inline** comment on a page (agent-authored discussion —
it does not change page content). Agents can't pick character offsets, so the
tool anchors by an exact `quoted_text` snippet: we read the page at HEAD, locate
the snippet, and require it to appear *exactly once* (no guessing). The thread is
attributed to the user driving the chat (`author_user_id = current_user()`) —
the agent acts on their behalf, so it's their comment — with `author_kind="agent"`
kept as provenance (the row records it was posted via an agent).

Visibility/permission mirrors human commenting: read access to the page is
enough to comment, resolved via `require_can("read", path)` against the calling
user (`current_user()`), the same principal the chat request authenticated as.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from app.auth import PermissionDenied, current_user, require_can
from app.llm.agents.tools.errors import ToolError
from app.models.comment import CommentAuthorKind
from app.wiki import comments as comments_repo, git as wiki_git, utils as wiki_utils


def _thread_link(doc_path: str, thread_root_id: str) -> str:
    encoded = "/".join(quote(seg) for seg in doc_path.split("/") if seg)
    return f"/app/wiki/{encoded}?comment={quote(thread_root_id, safe='')}"


def handle(args: dict[str, Any]) -> Any:
    try:
        path = wiki_utils.validate_doc_path(args.get("path"))
    except ToolError as exc:
        return {"error": str(exc)}

    body = args.get("body")
    if not isinstance(body, str) or not body.strip():
        return {"error": "body is required"}
    quoted = args.get("quoted_text")
    if not isinstance(quoted, str) or not quoted.strip():
        return {"error": "quoted_text is required — the exact snippet to anchor the comment to"}

    if not wiki_utils.file_exists(path):
        return {"error": f"file not found: {path}"}

    try:
        require_can("read", path)
    except PermissionDenied as exc:
        return {"error": str(exc)}

    head_sha = wiki_git.head_sha_for_path(path)
    if not head_sha:
        return {"error": f"could not resolve HEAD for {path}"}
    page = wiki_git.read_file(path, ref="HEAD")

    # Anchor to the quoted snippet — require an exact, unique occurrence so we
    # never guess where the comment belongs.
    idx = page.find(quoted)
    if idx < 0:
        return {
            "error": "quoted_text not found verbatim on the page; copy an exact "
            "snippet from the current body"
        }
    if page.find(quoted, idx + 1) != -1:
        return {
            "error": "quoted_text appears more than once; quote a longer, unique "
            "snippet so the comment anchors unambiguously"
        }

    user = current_user()
    row = comments_repo.create_thread(
        doc_path=path,
        body=body.strip(),
        author_user_id=user.id if user else None,
        anchor_sha=head_sha,
        start_offset=idx,
        end_offset=idx + len(quoted),
        quoted_text=quoted,
        author_kind=CommentAuthorKind.AGENT.value,
    )
    return {
        "comment_id": row["id"],
        "doc_path": path,
        "link": _thread_link(path, row["thread_root_id"]),
    }
