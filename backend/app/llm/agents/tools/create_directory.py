"""Handler for the `create_directory` tool. Spec lives in `create_directory.json`.

Creates a `.gitkeep`-marked empty folder in the wiki. Mirrors the
`POST /api/wiki/folder` endpoint so agents can create folders the
same way humans do in the explorer.
"""
from __future__ import annotations

from typing import Any

from app.wiki import utils as wiki_utils
from app.wiki import filesystem, git as wiki_git


def handle(args: dict[str, Any]) -> Any:
    try:
        raw = args.get("path")
        commit_message = args.get("commit_message")
        if not isinstance(raw, str) or not raw.strip():
            raise wiki_utils.ToolError("path is required")
        if not isinstance(commit_message, str) or not commit_message.strip():
            raise wiki_utils.ToolError("commit_message is required")

        cleaned = raw.strip().strip("/")
        if not cleaned:
            raise wiki_utils.ToolError("path is required")
        try:
            path = filesystem.safe_rel_path(cleaned)
        except ValueError as exc:
            raise wiki_utils.ToolError(f"invalid path: {exc}")
        if path.endswith(".md"):
            raise wiki_utils.ToolError("directory path must not end in .md")

        abs_path = filesystem.absolute(path)
        if abs_path.is_file():
            raise wiki_utils.ToolError(f"a file already exists at {path}")
        if abs_path.is_dir():
            raise wiki_utils.ToolError(f"directory already exists: {path}")

        sha = wiki_git.commit_file(
            f"{path}/.gitkeep", "", commit_message.strip(), author=wiki_utils.author_string()
        )
        return {"path": path, "sha": sha, "created": True}
    except wiki_utils.ToolError as exc:
        return {"error": str(exc)}
