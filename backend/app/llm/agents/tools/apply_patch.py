"""Handler for the `apply_patch` tool. Spec lives in `apply_patch.json`.

Line-anchored unified-diff editor. Calls into ``app.wiki.patch.apply``
which does the parsing, line-anchored match, and fuzzy fallback. This
handler is the tool-side adapter: argument validation, applying the
patch against its base, commit + fan-out.
"""
from __future__ import annotations

from typing import Any

from app.wiki import utils as wiki_utils
from app.wiki import git as wiki_git
from app.llm.agents.tools.errors import ToolError
from app.llm.errors import LLMError
from app.wiki import patch as wiki_patch
from app.models.wiki import ChangeKind, CommitMaxRetriesError


def handle(args: dict[str, Any]) -> Any:
    try:
        path = wiki_utils.validate_doc_path(args.get("path"))
        patch = args.get("patch")
        commit_message = args.get("commit_message")
        base_sha = args.get("base_sha")
        activity_ttl = wiki_utils.parse_expires_in_seconds(args.get("expires_in_seconds"))
        if not isinstance(patch, str) or not patch.strip():
            raise ToolError("patch is required (non-empty string)")
        if not isinstance(commit_message, str) or not commit_message.strip():
            raise ToolError("commit_message is required")
        if not isinstance(base_sha, str) or not base_sha.strip():
            return {
                "error": "base_sha_required",
                "message": (
                    "base_sha is required: the patch is applied against the "
                    "commit it was authored on, then 3-way merged against HEAD. "
                    "Read the doc and pass its sha as base_sha."
                ),
            }

        if not wiki_utils.file_exists(path):
            raise ToolError(f"file not found: {path}")

        # base_sha is the patch's true base: applying the hunks there keeps the
        # line anchors valid, yielding a full body that commit_and_fan_out
        # 3-way merges against current HEAD.
        try:
            base_body = wiki_git.read_file(path, ref=base_sha)
        except wiki_git.UnknownSha:
            return {
                "error": "base_sha_not_found",
                "message": (
                    "base_sha does not resolve to a known commit; "
                    "re-read the doc and pass its sha as base_sha."
                ),
            }

        try:
            new_body = wiki_patch.apply(base_body, patch)
        except wiki_patch.PatchError as exc:
            raise ToolError(str(exc))

        if new_body == base_body:
            raise ToolError(
                "patch produced no change (every hunk was a no-op)"
            )

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
