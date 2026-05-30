"""Handler for the `edit_doc` tool. Spec lives in `edit_doc.json`.

Surgical find-and-replace using the fuzzy chain in ``app.wiki.edit``.
"""
from __future__ import annotations

from typing import Any

from app.wiki import utils as wiki_utils
from app.wiki import edit as wiki_edit
from app.wiki import git as wiki_git
from app.llm.agents.tools.errors import ToolError
from app.llm.errors import LLMError
from app.models.wiki import ChangeKind, CommitMaxRetriesError


def handle(args: dict[str, Any]) -> Any:
    try:
        path = wiki_utils.validate_doc_path(args.get("path"))
        old_string = args.get("old_string")
        new_string = args.get("new_string")
        commit_message = args.get("commit_message")
        activity_ttl = wiki_utils.parse_expires_in_seconds(args.get("expires_in_seconds"))
        if not isinstance(old_string, str) or old_string == "":
            raise ToolError("old_string is required and must be non-empty")
        if not isinstance(new_string, str):
            raise ToolError("new_string is required (string)")
        if not isinstance(commit_message, str) or not commit_message.strip():
            raise ToolError("commit_message is required")
        replace_all = bool(args.get("replace_all", False))

        if not wiki_utils.file_exists(path):
            raise ToolError(f"file not found: {path}")

        # The replace targets current content; concurrent drift is reconciled
        # by the 3-way merge in commit_and_fan_out, and a vanished anchor
        # surfaces as a ReplaceError below.
        base_body = wiki_utils.read_existing(path)
        try:
            new_body = wiki_edit.replace(base_body, old_string, new_string, replace_all)
        except wiki_edit.ReplaceError as exc:
            raise ToolError(str(exc))

        try:
            result = wiki_utils.commit_and_fan_out(
                path=path, body=new_body, message=commit_message.strip(),
                change_kind=ChangeKind.EDIT,
                base_body=base_body,
                ai_merge=True,
                activity_ttl=activity_ttl,
            )
        except CommitMaxRetriesError as exc:
            return {
                "error": "stale_base",
                "message": "concurrent edits kept landing; max retries exceeded",
                "current_sha": exc.current_sha,
            }
        except LLMError as exc:
            return {"error": f"llm_error: {exc}"}

        if result is None:
            return {"path": path, "sha": wiki_git.head_sha_for_path(path), "no_change": True}

        return {
            "path": path,
            "sha": result.sha,
            "diff": wiki_utils.unified_diff(result.old_body, result.new_body, path),
            "broken_links": wiki_utils.broken_links(path, result.new_body),
        }
    except ToolError as exc:
        return {"error": str(exc)}
