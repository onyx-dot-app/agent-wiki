"""Manage MCP server connections used by the agent harness."""
from __future__ import annotations

from flask import Blueprint

from app.auth import login_required

bp = Blueprint("mcp", __name__)


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
