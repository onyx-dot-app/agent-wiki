"""Handler for the `read_page` tool. Spec lives in `read_page.json`.

Returns the full body of a single wiki doc plus the active agent
activity rows for that path. Registers a ``read`` activity in the
registry so co-occupant agents see each other.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.auth import PermissionDenied, require_can
from app.wiki import utils as wiki_utils
from app.llm.agents.tools.errors import ToolError
from app.wiki import agent_activity, git as wiki_git, page_views, update_policy


def handle(args: dict[str, Any]) -> Any:
    try:
        path = wiki_utils.validate_doc_path(args.get("path"))
    except ToolError as exc:
        return {"error": str(exc)}

    if not wiki_utils.file_exists(path):
        return {"error": f"file not found: {path}"}


    try:
        require_can("read", path)
    except PermissionDenied as exc:
        return {"error": str(exc)}

    page_views.note_view(path)

    try:
        body = wiki_git.read_file(path)
    except Exception as exc:
        return {"error": f"could not read {path}: {exc}"}

    wiki_utils.mark_doc_read(path)
    # A successful read is a "view" — stamped only after the body was
    # produced (a failed read must not mark the page as used).
    page_views.note_view(path)

    result: dict[str, Any] = {
        "path": path,
        "title": _derive_title(path, body),
        "body": body,
        "agents": [r.model_dump() for r in agent_activity.list_for_doc(path)],
    }
    # Surface the page's effective update instruction (incl. inherited from a
    # parent folder) so an agent editing this page can follow it.
    instruction = update_policy.resolve_for_path(path).update_instruction
    if instruction:
        result["update_instruction"] = instruction
    return result


def _derive_title(path: str, body: str) -> str:
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return Path(path).stem
