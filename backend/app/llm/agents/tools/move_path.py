"""Handler for the `move_path` tool. Spec lives in `move_path.json`.

Pure rename via ``git mv`` — no content rewrite. Reindexes every moved
``.md`` file under the new path so FTS stays consistent.
"""
from __future__ import annotations

from typing import Any

from app.llm.agents.tools import _doc_helpers as h
from app.wiki import filesystem, git as wiki_git, notify as wiki_notify


def handle(args: dict[str, Any]) -> Any:
    try:
        old_raw = args.get("old_path")
        new_raw = args.get("new_path")
        message = args.get("message")
        if not isinstance(old_raw, str) or not old_raw.strip():
            raise h.ToolError("old_path is required")
        if not isinstance(new_raw, str) or not new_raw.strip():
            raise h.ToolError("new_path is required")
        if not isinstance(message, str) or not message.strip():
            raise h.ToolError("message is required")

        try:
            old_rel = filesystem.safe_rel_path(old_raw.strip().strip("/"))
            new_rel = filesystem.safe_rel_path(new_raw.strip().strip("/"))
        except ValueError as exc:
            raise h.ToolError(f"invalid path: {exc}")
        if not old_rel or not new_rel:
            raise h.ToolError("paths must be non-empty")
        if old_rel == new_rel:
            raise h.ToolError("old_path and new_path are identical")

        old_abs = filesystem.absolute(old_rel)
        new_abs = filesystem.absolute(new_rel)
        if not old_abs.exists():
            raise h.ToolError(f"old_path not found: {old_rel}")
        if new_abs.exists():
            raise h.ToolError(f"new_path already exists: {new_rel}")
        if old_abs.is_file() and old_rel.endswith(".md") and not new_rel.endswith(".md"):
            raise h.ToolError("renaming a .md file requires new_path to end in .md")
        if old_abs.is_dir() and new_rel.endswith(".md"):
            raise h.ToolError("renaming a directory requires new_path to not end in .md")

        author = h.author_string()
        sha, moves = wiki_git.move_path(
            old_rel, new_rel, message.strip(), author=author
        )
        wiki_notify.after_path_move(moves, sha, author)

        return {
            "old_path": old_rel,
            "new_path": new_rel,
            "sha": sha,
            "moved": [{"old": o, "new": n} for o, n in moves],
        }
    except h.ToolError as exc:
        return {"error": str(exc)}
