"""Handler for the `move_path` tool. Spec lives in `move_path.json`.

Pure rename via ``git mv`` — no content rewrite. Reindexes every moved
``.md`` file under the new path so FTS stays consistent.
"""
from __future__ import annotations

from typing import Any

from app.models.wiki import PathMove
from app.wiki import utils as wiki_utils
from app.llm.agents.tools.errors import ToolError
from app.wiki import coedit, filesystem, git as wiki_git, notify as wiki_notify


def handle(args: dict[str, Any]) -> Any:
    try:
        old_raw = args.get("old_path")
        new_raw = args.get("new_path")
        commit_message = args.get("commit_message")
        if not isinstance(old_raw, str) or not old_raw.strip():
            raise ToolError("old_path is required")
        if not isinstance(new_raw, str) or not new_raw.strip():
            raise ToolError("new_path is required")
        if not isinstance(commit_message, str) or not commit_message.strip():
            raise ToolError("commit_message is required")

        try:
            old_rel = filesystem.safe_rel_path(old_raw.strip().strip("/"))
            new_rel = filesystem.safe_rel_path(new_raw.strip().strip("/"))
        except ValueError as exc:
            raise ToolError(f"invalid path: {exc}")
        if not old_rel or not new_rel:
            raise ToolError("paths must be non-empty")
        if old_rel == new_rel:
            raise ToolError("old_path and new_path are identical")

        old_abs = filesystem.absolute(old_rel)
        new_abs = filesystem.absolute(new_rel)
        if not old_abs.exists():
            raise ToolError(f"old_path not found: {old_rel}")
        if new_abs.exists():
            raise ToolError(f"new_path already exists: {new_rel}")
        blocking = coedit.blocking_active_session_path(new_rel)
        if blocking is not None:
            raise ToolError(
                f"someone is editing an unsaved draft at {blocking!r}; "
                "pick a different name or wait for it to be saved"
            )
        if old_abs.is_file() and old_rel.endswith(".md") and not new_rel.endswith(".md"):
            raise ToolError("renaming a .md file requires new_path to end in .md")
        if old_abs.is_dir() and new_rel.endswith(".md"):
            raise ToolError("renaming a directory requires new_path to not end in .md")

        author = wiki_utils.author_string()
        sha, moves = wiki_git.move_path(
            old_rel, new_rel, commit_message.strip(), author=author
        )
        wiki_notify.after_path_move(
            moves, sha, author, root_move=PathMove(old=old_rel, new=new_rel)
        )

        return {
            "old_path": old_rel,
            "new_path": new_rel,
            "sha": sha,
            "moved": [{"old": mv.old, "new": mv.new} for mv in moves],
        }
    except ToolError as exc:
        return {"error": str(exc)}
