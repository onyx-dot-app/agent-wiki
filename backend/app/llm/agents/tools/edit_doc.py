"""Handler for the `edit_doc` tool. Spec lives in `edit_doc.json`.

Surgical find-and-replace using the fuzzy chain in ``app.wiki.edit``.
"""
from __future__ import annotations

from typing import Any

from app.llm.agents.tools import _doc_helpers as h
from app.wiki import edit as wiki_edit


def handle(args: dict[str, Any]) -> Any:
    try:
        rel = h.validate_doc_path(args.get("path"))
        old_string = args.get("old_string")
        new_string = args.get("new_string")
        commit_message = args.get("commit_message")
        if not isinstance(old_string, str) or old_string == "":
            raise h.ToolError("old_string is required and must be non-empty")
        if not isinstance(new_string, str):
            raise h.ToolError("new_string is required (string)")
        if not isinstance(commit_message, str) or not commit_message.strip():
            raise h.ToolError("commit_message is required")
        replace_all = bool(args.get("replace_all", False))

        if not h.file_exists(rel):
            raise h.ToolError(f"file not found: {rel}")
        h.assert_read_before_write(rel)

        old_body = h.read_existing(rel)
        try:
            new_body = wiki_edit.replace(old_body, old_string, new_string, replace_all)
        except wiki_edit.ReplaceError as exc:
            raise h.ToolError(str(exc))

        sha = h.commit_and_fan_out(rel, new_body, commit_message.strip(), change_kind="edit")

        return {
            "path": rel,
            "sha": sha,
            "diff": h.unified_diff(old_body, new_body, rel),
            "broken_links": h.broken_links(rel, new_body),
        }
    except h.ToolError as exc:
        return {"error": str(exc)}
