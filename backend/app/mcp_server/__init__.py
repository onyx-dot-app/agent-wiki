"""Inbound MCP server — exposes the wiki *as* an MCP server to external
coding agents (Claude Code, Cursor, Codex, …).

Distinct from the outbound surface in ``app/api/mcp_connections.py``,
which manages the agent harness's *use* of *other* MCP servers.

Design: ``local_data/wiki/mcp-server/mcp-server.md``.

Phase 2 has landed: bearer-token auth, in-memory session registry, and a
JSON-RPC dispatcher that handles the ``initialize`` handshake and an
empty ``tools/list``. Tools, resources, and pub-sub come in later
phases; this package will grow ``tools/`` and ``pubsub.py`` modules as
those land.
"""
