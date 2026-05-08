"""Build the MCP server. Reuses :mod:`app.llm.agents.tools` handlers verbatim.

Tool handlers in ``app.llm.agents.tools`` already implement everything we need:
input validation, read-before-write enforcement (via the
``seen_doc_paths`` ContextVar), commit + reindex + trigger fan-out, and
``{"error": str}`` envelope on failure. The MCP server is a thin adapter that
loads a filtered subset of those specs and dispatches to the same registry.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server import Server
from mcp.types import TextContent, Tool

from app.llm.agents import tools as tool_registry
from app.llm.agents._session import seen_doc_paths

log = logging.getLogger(__name__)

D2_TOOL_NAMES: frozenset[str] = frozenset({"search_wiki", "read_page", "edit_doc", "write_doc"})


def _exposed_specs() -> list[dict[str, Any]]:
    return [s for s in tool_registry.TOOL_SPECS if s["name"] in D2_TOOL_NAMES]


def _spec_to_tool(spec: dict[str, Any]) -> Tool:
    return Tool(
        name=spec["name"],
        description=spec["description"],
        inputSchema=spec["input_schema"],
    )


def _record_seen_path(name: str, result: Any) -> None:
    if name != "read_page":
        return
    seen = seen_doc_paths.get()
    if seen is None or not isinstance(result, dict):
        return
    path = result.get("path")
    if isinstance(path, str) and path:
        seen.add(path)


def build_server() -> Server:
    server: Server = Server("agent-wiki")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [_spec_to_tool(s) for s in _exposed_specs()]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        if name not in D2_TOOL_NAMES:
            return [TextContent(type="text", text=json.dumps({"error": "unknown tool: %s" % name}))]
        try:
            result = tool_registry.dispatch(name, arguments)
        except Exception as exc:
            log.exception("mcp tool %s raised", name)
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}))]
        _record_seen_path(name, result)
        body = result if isinstance(result, str) else json.dumps(result)
        return [TextContent(type="text", text=body)]

    return server
