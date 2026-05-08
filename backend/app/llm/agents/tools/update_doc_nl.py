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

from app.llm.agents import document_updater
from app.llm.agents.tools import _doc_helpers as h
from app.llm.errors import LLMError
from app.wiki import git as wiki_git

log = logging.getLogger(__name__)


def handle(args: dict[str, Any]) -> Any:
    try:
        rel = h.validate_doc_path(args.get("path"))
        instruction = args.get("instruction")
        base_sha = args.get("base_sha")
        if not isinstance(instruction, str) or not instruction.strip():
            raise h.ToolError("instruction is required (non-empty string)")
        if base_sha is not None and not isinstance(base_sha, str):
            raise h.ToolError("base_sha must be a string when provided")

        if not h.file_exists(rel):
            raise h.ToolError(f"file not found: {rel}")
        h.assert_read_before_write(rel)

        head_sha = wiki_git.head_sha_for_path(rel)
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

        old_body = h.read_existing(rel)
        try:
            new_body = document_updater.run(
                doc_id=rel,
                current_body=old_body,
                payload={"instruction": instruction.strip()},
                source="update_doc_nl",
            )
        except LLMError as exc:
            log.warning("update_doc_nl LLM error on %s: %s", rel, exc)
            return {"error": f"llm_error: {exc}"}

        if new_body is None or new_body == old_body:
            return {
                "path": rel,
                "committed": False,
                "reason": "no_change",
                "sha": head_sha,
            }

        sha = h.commit_and_fan_out(
            rel,
            new_body,
            f"Doc update: {instruction.strip()[:80]}",
            change_kind="edit",
        )
        return {
            "path": rel,
            "committed": True,
            "sha": sha,
            "diff": h.unified_diff(old_body, new_body, rel),
            "broken_links": h.broken_links(rel, new_body),
        }
    except h.ToolError as exc:
        return {"error": str(exc)}
