"""HTTP shapes for the inbound MCP token surface (``/api/mcp/tokens``)
and the JSON-RPC envelope the MCP transport accepts.

The MCP server transport itself only validates the envelope here; the
``params`` payload is shaped per-method inside ``app.mcp_server`` and
isn't worth a discriminated-union at the FastAPI layer.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JsonRpcRequest(BaseModel):
    """JSON-RPC 2.0 envelope.

    ``id`` is intentionally absent for notifications; the dispatcher
    distinguishes notifications from requests by checking
    ``model_fields_set`` rather than the value (``id == null`` is a
    legal request id in JSON-RPC and is not a notification). ``params``
    is left as a free-form object because individual MCP methods shape
    it themselves; FastAPI still rejects non-object request bodies.
    """

    model_config = ConfigDict(extra="allow")

    jsonrpc: str | None = None
    method: str | None = None
    id: str | int | None = None
    params: dict[str, Any] | None = None


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
