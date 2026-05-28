"""Handler for the `update_doc_nl` tool. Spec lives in `update_doc_nl.json`.

Sync dispatch to the document-updater sub-agent. The full MCP design
(`local_data/wiki/mcp-server/mcp-server.md`) calls for an async jobs
queue with idempotency keys + push notifications on completion — that's
deferred to the MCP layer. For now this tool blocks until the sub-agent
returns and either commits inline or reports NO_CHANGE.
"""
from __future__ import annotations

import logging
from typing import Any

from app.llm.agents import nl_updater
from app.wiki import utils as wiki_utils
from app.llm.errors import LLMError
from app.wiki import git as wiki_git
from app.models.wiki import ChangeKind

log = logging.getLogger(__name__)


def handle(args: dict[str, Any]) -> Any:
    try:
        path = wiki_utils.validate_doc_path(args.get("path"))
        instruction = args.get("instruction")
        base_sha = args.get("base_sha")
        activity_ttl = wiki_utils.parse_expires_in_seconds(args.get("expires_in_seconds"))
        if not isinstance(instruction, str) or not instruction.strip():
            raise wiki_utils.ToolError("instruction is required (non-empty string)")
        if base_sha is not None and not isinstance(base_sha, str):
            raise wiki_utils.ToolError("base_sha must be a string when provided")

        if not wiki_utils.file_exists(path):
            raise wiki_utils.ToolError(f"file not found: {path}")

        head_sha = wiki_git.head_sha_for_path(path)
        if base_sha and base_sha != head_sha:
            return {
                "error": "stale_base",
                "base_sha": base_sha,
                "current_sha": head_sha,
                "message": (
                    "the file has changed since base_sha; re-read with "
                    "read_doc and re-issue the instruction"
                ),
            }

        old_body = wiki_utils.read_existing(path)
        try:
            new_body = nl_updater.process_instruction(
                wiki_path=path,
                current_body=old_body,
                payload={"instruction": instruction.strip()},
                source="update_doc_nl",
            )
        except LLMError as exc:
            log.warning("update_doc_nl LLM error on %s: %s", path, exc)
            return {"error": f"llm_error: {exc}"}

        if new_body is None or new_body == old_body:
            return {
                "path": path,
                "committed": False,
                "reason": "no_change",
                "sha": head_sha,
            }

        sha = wiki_utils.commit_and_fan_out(
            path,
            new_body,
            f"Doc update: {instruction.strip()[:80]}",
            change_kind=ChangeKind.EDIT,
            activity_ttl=activity_ttl,
        )
        return {
            "path": path,
            "committed": True,
            "sha": sha,
            "diff": wiki_utils.unified_diff(old_body, new_body, path),
            "broken_links": wiki_utils.broken_links(path, new_body),
        }
    except wiki_utils.ToolError as exc:
        return {"error": str(exc)}
