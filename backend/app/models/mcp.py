"""HTTP shapes for the inbound MCP token surface (``/api/mcp/tokens``).

The JSON-RPC envelope the MCP transport accepts isn't modeled here —
``app/api/mcp_server.py`` reads the raw body so envelope validation
errors surface as JSON-RPC error responses (code -32600) per spec,
rather than FastAPI's pydantic-validation envelope.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class CreateMcpTokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class McpTokenSummary(BaseModel):
    """A token in the list — never includes the raw value or the hash."""

    id: str
    name: str
    created_at: str
    last_used_at: str | None


class McpTokenList(BaseModel):
    tokens: list[McpTokenSummary]


class CreatedMcpToken(BaseModel):
    """Returned exactly once at creation. ``token`` is the plaintext the
    user must copy now — there is no second chance."""

    id: str
    name: str
    created_at: str
    token: str
