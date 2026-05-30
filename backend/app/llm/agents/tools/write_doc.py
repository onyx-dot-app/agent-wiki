"""Handler for the `write_doc` tool. Spec lives in `write_doc.json`.

Full-body overwrite (or create). Use sparingly — prefer ``edit_doc`` for
surgical changes.
"""
from __future__ import annotations

from typing import Any

from app.wiki import utils as wiki_utils
from app.wiki import git as wiki_git
from app.llm.agents.tools.errors import ToolError
from app.llm.errors import LLMError
from app.models.wiki import ChangeKind, CommitMaxRetriesError


def handle(args: dict[str, Any]) -> Any:
    try:
        path = wiki_utils.validate_doc_path(args.get("path"))
        body = args.get("body")
        commit_message = args.get("commit_message")
        base_sha = args.get("base_sha")
        activity_ttl = wiki_utils.parse_expires_in_seconds(args.get("expires_in_seconds"))
        if not isinstance(body, str):
            raise ToolError("body is required (string)")
        if not isinstance(commit_message, str) or not commit_message.strip():
            raise ToolError("commit_message is required")
        if base_sha is not None and not isinstance(base_sha, str):
            raise ToolError("base_sha must be a string when provided")

        existed = wiki_utils.file_exists(path)
        if existed:
            # Full-body overwrite requires base_sha so we can 3-way merge
            # if a concurrent commit landed between when the agent read the
            # doc and when it calls write_doc.
            if base_sha is None:
                return {
                    "error": "base_sha_required_for_overwrite",
                    "message": (
                        "write_doc on an existing file requires base_sha "
                        "(the sha you last read). Re-read the doc and "
                        "pass its sha as base_sha."
                    ),
                }
            # base_sha is the merge base: the 3-way merge inside
            # commit_and_fan_out reconciles drift against it, so it must
            # resolve to a real commit.
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
                result = wiki_utils.commit_and_fan_out(
                    path=path, body=body, message=commit_message.strip(),
                    change_kind=ChangeKind.EDIT,  # new files take the else branch below
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
                "created": False,
                "diff": wiki_utils.unified_diff(result.old_body, result.new_body, path),
                "broken_links": wiki_utils.broken_links(path, result.new_body),
            }
        else:
            # New file: no base to merge against, so this always commits.
            result = wiki_utils.commit_and_fan_out(
                path=path, body=body, message=commit_message.strip(),
                change_kind=ChangeKind.CREATE, activity_ttl=activity_ttl,
            )
            if result is None:
                raise RuntimeError("commit_and_fan_out returned None on a no-base commit")
            return {
                "path": path,
                "sha": result.sha,
                "created": True,
                "diff": wiki_utils.unified_diff("", body, path),
                "broken_links": wiki_utils.broken_links(path, body),
            }
    except ToolError as exc:
        return {"error": str(exc)}
