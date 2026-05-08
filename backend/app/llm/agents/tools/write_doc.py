"""Handler for the `write_doc` tool. Spec lives in `write_doc.json`.

Full-body overwrite (or create). Use sparingly — prefer ``edit_doc`` for
surgical changes.
"""
from __future__ import annotations

from typing import Any

from app.llm.agents.tools import _doc_helpers as h


def handle(args: dict[str, Any]) -> Any:
    try:
        rel = h.validate_doc_path(args.get("path"))
        body = args.get("body")
        message = args.get("message")
        if not isinstance(body, str):
            raise h.ToolError("body is required (string)")
        if not isinstance(message, str) or not message.strip():
            raise h.ToolError("message is required")

        existed = h.file_exists(rel)
        if existed:
            h.assert_read_before_write(rel)
            old = h.read_existing(rel)
        else:
            old = ""

        change_kind = "edit" if existed else "create"
        sha = h.commit_and_fan_out(rel, body, message.strip(), change_kind=change_kind)

        return {
            "path": rel,
            "sha": sha,
            "created": not existed,
            "diff": h.unified_diff(old, body, rel),
            "broken_links": h.broken_links(rel, body),
        }
    except h.ToolError as exc:
        return {"error": str(exc)}
