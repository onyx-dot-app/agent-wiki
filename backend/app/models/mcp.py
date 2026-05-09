"""HTTP shapes for the inbound MCP token surface (``/api/mcp/tokens``).

The MCP server transport itself doesn't go through these models — it
speaks JSON-RPC over Streamable HTTP and uses the ``mcp`` SDK's framing.
These shapes only cover the user-facing CRUD a person uses to mint and
revoke API keys.
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
