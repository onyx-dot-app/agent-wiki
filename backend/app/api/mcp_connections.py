"""Manage outbound MCP server connections — the user-managed list of
external MCP servers our in-process agent harness consumes as a
*client*. The inbound surface (where this app exposes itself *as* an
MCP server to external coding agents) lives separately under
``app/mcp_server/`` and ``app/api/mcp_tokens.py``.
"""
from __future__ import annotations

from flask import Blueprint

from app.auth import login_required

bp = Blueprint("mcp_connections", __name__)


@bp.get("")
@login_required
def list_connections():
    raise NotImplementedError


@bp.post("")
@login_required
def create_connection():
    # body: {name, transport, config}
    raise NotImplementedError


@bp.delete("/<conn_id>")
@login_required
def delete_connection(conn_id: str):
    raise NotImplementedError
