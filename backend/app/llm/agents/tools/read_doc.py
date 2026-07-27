"""Handler for the `read_doc` tool. Spec lives in `read_doc.json`.

The MCP-aware read tool. Differs from ``read_page`` only in that it
accepts an optional ``sha`` for historical reads.
"""
from __future__ import annotations

from typing import Any

from app.wiki import utils as wiki_utils
from app.llm.agents.tools.errors import ToolError
from app.wiki import agent_activity, git as wiki_git, provenance, update_policy


def handle(args: dict[str, Any]) -> Any:
    try:
        path = wiki_utils.validate_doc_path(args.get("path"))
    except ToolError as exc:
        return {"error": str(exc)}

    raw_sha = args.get("sha")
    if raw_sha is not None and not isinstance(raw_sha, str):
        return {"error": "sha must be a string"}
    sha = raw_sha.strip() if isinstance(raw_sha, str) and raw_sha.strip() else None

    head_sha = wiki_git.head_sha_for_path(path)
    if sha is None and not wiki_utils.file_exists(path):
        return {"error": f"file not found: {path}"}

    from app.auth import PermissionDenied, require_can  # noqa: PLC0415

    try:
        require_can("read", path)
    except PermissionDenied as exc:
        return {"error": str(exc)}

    ref = sha or "HEAD"
    try:
        body = wiki_git.read_file(path, ref=ref)
    except wiki_git.UnknownSha:
        return {
            "error": (
                f"sha_not_found: {path} not present at {ref}"
                if sha
                else f"could not read {path}"
            )
        }
    except Exception as exc:  # pragma: no cover — git wrapper is well-defined
        return {"error": f"could not read {path}@{ref}: {exc}"}

    is_head = sha is None or sha == head_sha
    agents: list[dict[str, Any]] = []
    if is_head:
        wiki_utils.mark_doc_read(path)
        agents = [r.model_dump() for r in agent_activity.list_for_doc(path)]

    result: dict[str, Any] = {
        "path": path,
        "body": body,
        "sha": sha or head_sha,
        "is_head": is_head,
        "agents": agents,
    }
    # Surface the page's effective update instruction (incl. inherited from a
    # parent folder) so an agent editing this page can follow it.
    instruction = update_policy.resolve_for_path(path).update_instruction
    if instruction:
        result["update_instruction"] = instruction

    if is_head:
        attr = provenance.head_attribution(path, head_sha)
    else:
        # A historical read resolves from the ledger only.
        attr = provenance.for_commits([sha], path).get(sha) if sha else None
    result["attribution"] = attr.model_dump() if attr is not None else None
    # Sources are page-level at current HEAD, including on a historical read.
    result["sources"] = [sr.model_dump() for sr in provenance.sources_for_path(path)]
    return result
