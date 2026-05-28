"""Handler for the `edit_doc` tool. Spec lives in `edit_doc.json`.

Surgical find-and-replace using the fuzzy chain in ``app.wiki.edit``.
"""
from __future__ import annotations

from typing import Any

from app.wiki import utils as wiki_utils
from app.wiki import edit as wiki_edit


def handle(args: dict[str, Any]) -> Any:
    try:
        path = wiki_utils.validate_doc_path(args.get("path"))
        old_string = args.get("old_string")
        new_string = args.get("new_string")
        commit_message = args.get("commit_message")
        base_sha = args.get("base_sha")
        activity_ttl = wiki_utils.parse_expires_in_seconds(args.get("expires_in_seconds"))
        if not isinstance(old_string, str) or old_string == "":
            raise wiki_utils.ToolError("old_string is required and must be non-empty")
        if not isinstance(new_string, str):
            raise wiki_utils.ToolError("new_string is required (string)")
        if not isinstance(commit_message, str) or not commit_message.strip():
            raise wiki_utils.ToolError("commit_message is required")
        if base_sha is not None and not isinstance(base_sha, str):
            raise wiki_utils.ToolError("base_sha must be a string when provided")
        replace_all = bool(args.get("replace_all", False))

        if not wiki_utils.file_exists(path):
            raise wiki_utils.ToolError(f"file not found: {path}")

        base_body = wiki_utils.read_existing(path)
        try:
            new_body = wiki_edit.replace(base_body, old_string, new_string, replace_all)
        except wiki_edit.ReplaceError as exc:
            stale = wiki_utils.assert_base_sha(path, base_sha)
            if stale is not None:
                return stale
            raise wiki_utils.ToolError(str(exc))

        try:
            result = wiki_utils.commit_with_ai_rebase(
                path, commit_message.strip(),
                base_body=base_body,
                new_body=new_body,
                activity_ttl=activity_ttl,
            )
        except wiki_utils.AiRebaseMaxRetriesException as exc:
            return {
                "error": "stale_base",
                "message": "concurrent edits kept landing; max retries exceeded",
                "current_sha": exc.current_sha,
            }

        if result is None:
            raise wiki_utils.ToolError("edit produced no change")

        return {
            "path": path,
            "sha": result.sha,
            "diff": wiki_utils.unified_diff(result.old_body, result.new_body, path),
            "broken_links": wiki_utils.broken_links(path, result.new_body),
        }
    except wiki_utils.ToolError as exc:
        return {"error": str(exc)}
