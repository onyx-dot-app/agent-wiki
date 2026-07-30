"""Shared agentic substrate for LLM detectors.

The loop every LLM detector runs: hand the model one same-audience bucket's
file tree plus its candidates, give it bounded read/search tools, and take
exactly one ``finish`` call as the verdict. Detector-specific judgment lives
entirely in the caller's prompt and finish schema; this module owns the
mechanics — transcript management, step caps, tool dispatch, and the
audience-isolation filter on search results (restricted paths/titles must
never enter the transcript or a proposal's evidence text, which the
candidate page's reviewers may not be cleared for).

Callers catch ``LLMError`` themselves: one unconfigured/broken LLM must not
sink the sweep's mechanical detectors, and the right degradation message is
per-detector.
"""
from __future__ import annotations

import json
import logging
from typing import Any, cast

from app.db import fts
from app.llm import client as llm_client
from app.wiki import git
from app.wiki.automanage import fingerprint

log = logging.getLogger(__name__)

# Bound on the agent pass (a sweep must terminate whatever the model does).
MAX_STEPS = 24


def read_and_search_tools(finish_schema: dict[str, Any]) -> list[dict[str, Any]]:
    """The standard LLM-detector toolset: read a candidate, search the wiki,
    and ``finish`` with the caller's verdict schema."""
    return [
        {
            "name": "read_page",
            "description": "Read the current body of a candidate page.",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
        {
            "name": "search_wiki",
            "description": "Keyword-search the wiki (title + body).",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
        {
            "name": "finish",
            "description": "Deliver the final verdict. Call exactly once.",
            "input_schema": finish_schema,
        },
    ]


def bucket_fingerprint(candidates: list[str]) -> str:
    """The audience fingerprint of a same-audience bucket — any member's
    per-path fingerprint is the bucket's. (Per-path, not
    ``combined_fingerprint``, which re-hashes and never matches one.)"""
    return fingerprint.fingerprints_for_paths([candidates[0]])[candidates[0]]


def run_agent(
    *,
    detector_name: str,
    system: str,
    user_content: str,
    candidates: set[str],
    audience_fp: str,
    finish_schema: dict[str, Any],
) -> list[dict[str, Any]]:
    """Run the bounded agent loop; return ``finish``'s ``proposals`` list
    (raw — the caller validates each item), or ``[]`` on step-cap exhaustion.
    Raises ``LLMError`` through to the caller."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]
    tools = read_and_search_tools(finish_schema)
    for _ in range(MAX_STEPS):
        result = llm_client.complete(messages, tools=tools)
        if not result.tool_calls:
            # A text-only turn is a stall; nudge toward finish.
            messages.append({"role": "assistant", "content": result.text})
            messages.append(
                {"role": "user", "content": "Call finish with your proposals."}
            )
            continue
        messages.append(
            {
                "role": "assistant",
                "content": result.text,
                "tool_calls": [
                    {"id": c.id, "name": c.name, "arguments": c.arguments}
                    for c in result.tool_calls
                ],
            }
        )
        for call in result.tool_calls:
            if call.name == "finish":
                return cast(
                    "list[dict[str, Any]]",
                    call.arguments.get("proposals") or [],
                )
            out = dispatch_tool(
                detector_name, call.name, call.arguments, candidates, audience_fp
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(out),
                }
            )
    log.warning("%s: agent pass hit step cap without finish", detector_name)
    return []


def dispatch_tool(
    detector_name: str,
    name: str,
    args: dict[str, Any],
    candidates: set[str],
    audience_fp: str,
) -> Any:
    if name == "read_page":
        path = str(args.get("path", ""))
        if path not in candidates:
            return {"error": "not a candidate page"}
        body = git.read_file_opt(path)
        return (
            {"path": path, "body": body}
            if body is not None
            else {"error": "page no longer exists"}
        )
    if name == "search_wiki":
        hits = fts.search(
            str(args.get("query", "")), limit=16, is_admin=True,
            apply_visibility=False,
        )
        # Same-audience rule (as pairing detectors): never surface a page
        # across a visibility boundary.
        paths = [h.path for h in hits]
        fps = fingerprint.fingerprints_for_paths(paths) if paths else {}
        return [
            {"path": h.path, "title": h.title}
            for h in hits
            if fps.get(h.path) == audience_fp
        ][:8]
    return {"error": f"unknown tool {name!r}"}
