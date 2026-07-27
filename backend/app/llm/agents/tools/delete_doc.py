"""Handler for the `delete_doc` tool. Spec lives in `delete_doc.json`.

Soft-deletes a single wiki page: the same Trash flow as the UI delete
(``DELETE /file``) — a move into the hidden trash location with the full
``after_doc_trashed`` lifecycle, restorable from Trash for 30 days. Pages
only: folder deletion has a bigger blast radius and stays a human action.
"""
from __future__ import annotations

from typing import Any

from app.auth import PermissionDenied, require_can
from app.models.wiki import PathMove
from app.wiki import git as wiki_git
from app.wiki import notify, trash
from app.wiki import utils as wiki_utils
from app.llm.agents.tools.errors import ToolError


def handle(args: dict[str, Any]) -> Any:
    try:
        path = wiki_utils.validate_doc_path(args.get("path"))
    except ToolError as exc:
        return {"error": str(exc)}

    if not wiki_utils.file_exists(path):
        return {"error": f"file not found: {path}"}

    try:
        require_can("write", path)
    except PermissionDenied as exc:
        return {"error": str(exc)}

    author = wiki_utils.author_string()
    trash_id = trash.new_trash_id()
    dest = trash.trash_location(trash_id, path)
    sha, moves = wiki_git.move_path(
        path, dest, trash.trash_commit_message(path), author=author
    )
    notify.after_doc_trashed(
        moves, sha, author, root_move=PathMove(old=path, new=dest)
    )
    return {
        "deleted": path,
        "sha": sha,
        "trash_id": trash_id,
        "note": "moved to Trash — restorable for 30 days",
    }
