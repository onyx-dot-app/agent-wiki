"""Handler for the `multi_edit` tool. Spec lives in `multi_edit.json`.

Atomic batch edits: all replaces apply to the running body in memory, and
only on full success do we commit. Any failure aborts with no write.
"""
from __future__ import annotations

from typing import Any, NamedTuple, cast

from app.wiki import utils as wiki_utils
from app.wiki import edit as wiki_edit
from app.wiki import git as wiki_git
from app.llm.agents.tools.errors import ToolError
from app.llm.errors import LLMError
from app.models.wiki import AiRebaseMaxRetriesError


class _EditOp(NamedTuple):
    old_string: str
    new_string: str
    replace_all: bool


def handle(args: dict[str, Any]) -> Any:
    try:
        path = wiki_utils.validate_doc_path(args.get("path"))
        edits_raw = args.get("edits")
        commit_message = args.get("commit_message")
        base_sha = args.get("base_sha")
        activity_ttl = wiki_utils.parse_expires_in_seconds(args.get("expires_in_seconds"))
        if not isinstance(edits_raw, list) or not edits_raw:
            raise ToolError("edits must be a non-empty array")
        if not isinstance(commit_message, str) or not commit_message.strip():
            raise ToolError("commit_message is required")
        if base_sha is not None and not isinstance(base_sha, str):
            raise ToolError("base_sha must be a string when provided")
        edits = cast(list[Any], edits_raw)

        # Validate edit shapes up front before touching disk.
        parsed: list[_EditOp] = []
        for i, edit in enumerate(edits):
            if not isinstance(edit, dict):
                raise ToolError(f"edit #{i + 1}: must be an object")
            edit_dict = cast(dict[str, Any], edit)
            old_string = edit_dict.get("old_string")
            new_string = edit_dict.get("new_string")
            if not isinstance(old_string, str) or old_string == "":
                raise ToolError(f"edit #{i + 1}: old_string is required and non-empty")
            if not isinstance(new_string, str):
                raise ToolError(f"edit #{i + 1}: new_string is required (string)")
            parsed.append(_EditOp(old_string, new_string, bool(edit_dict.get("replace_all", False))))

        if not wiki_utils.file_exists(path):
            raise ToolError(f"file not found: {path}")

        stale = wiki_utils.assert_base_sha(path, base_sha)
        if stale is not None:
            return stale

        base_body = wiki_utils.read_existing(path)
        new_body = base_body
        try:
            for i, op in enumerate(parsed):
                try:
                    new_body = wiki_edit.replace(new_body, op.old_string, op.new_string, op.replace_all)
                except wiki_edit.ReplaceError as exc:
                    raise wiki_edit.ReplaceError(f"edit #{i + 1}: {exc}") from exc
        except wiki_edit.ReplaceError as exc:
            raise ToolError(str(exc))

        if new_body == base_body:
            raise ToolError("edits produced no change")

        try:
            result = wiki_utils.commit_with_ai_rebase(
                path, commit_message.strip(),
                base_body=base_body,
                new_body=new_body,
                activity_ttl=activity_ttl,
            )
        except AiRebaseMaxRetriesError as exc:
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
            "applied_count": len(parsed),
            "diff": wiki_utils.unified_diff(result.old_body, result.new_body, path),
            "broken_links": wiki_utils.broken_links(path, result.new_body),
        }
    except ToolError as exc:
        return {"error": str(exc)}
