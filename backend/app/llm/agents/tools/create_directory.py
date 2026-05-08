"""Handler for the `create_directory` tool. Spec lives in `create_directory.json`.

Creates a `.gitkeep`-marked empty folder in the wiki. Mirrors the
`POST /api/documents/folder` endpoint so agents can create folders the
same way humans do in the explorer.
"""
from __future__ import annotations

from typing import Any

from app.llm.agents.tools import _doc_helpers as h
from app.wiki import filesystem, git as wiki_git


def handle(args: dict[str, Any]) -> Any:
    try:
        raw = args.get("path")
        commit_message = args.get("commit_message")
        if not isinstance(raw, str) or not raw.strip():
            raise h.ToolError("path is required")
        if not isinstance(commit_message, str) or not commit_message.strip():
            raise h.ToolError("commit_message is required")

        cleaned = raw.strip().strip("/")
        if not cleaned:
            raise h.ToolError("path is required")
        try:
            rel = filesystem.safe_rel_path(cleaned)
        except ValueError as exc:
            raise h.ToolError(f"invalid path: {exc}")
        if rel.endswith(".md"):
            raise h.ToolError("directory path must not end in .md")

        abs_path = filesystem.absolute(rel)
        if abs_path.is_file():
            raise h.ToolError(f"a file already exists at {rel}")
        if abs_path.is_dir():
            raise h.ToolError(f"directory already exists: {rel}")

        sha = wiki_git.commit_file(
            f"{rel}/.gitkeep", "", commit_message.strip(), author=h.author_string()
        )
        return {"path": rel, "sha": sha, "created": True}
    except h.ToolError as exc:
        return {"error": str(exc)}
