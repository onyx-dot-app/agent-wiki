"""MCP server exposing the agent-wiki tool surface to external agents.

Direction: wiki-IS-MCP-server. Claude Code (or Craft, etc.) connects in over
stdio and drives the wiki via the same tool handlers the in-app chat agent
uses. Independent of ``app/api/mcp.py``, which is the outbound (wiki-USES-
MCP-clients) blueprint.
"""

from __future__ import annotations

from app.mcp_server.server import build_server

__all__ = ["build_server"]
