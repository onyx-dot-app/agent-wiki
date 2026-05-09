"""User-facing CRUD for inbound MCP API tokens.

Mounted at ``/api/mcp/tokens``. Every route is ``@login_required`` — a
user can only see and revoke their own tokens. Admin sees their own too;
there is no admin-wide "all tokens" view in v1 (out of scope per the
design doc).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.auth import current_user, login_required
from app.auth import mcp_tokens as tokens_repo
from app.models._helpers import error, parse_body
from app.models.mcp import (
    CreateMcpTokenRequest,
    CreatedMcpToken,
    McpTokenList,
    McpTokenSummary,
)

bp = Blueprint("mcp_tokens", __name__)


@bp.get("")
@login_required
def list_tokens():
    user = current_user()
    assert user is not None
    rows = tokens_repo.list_for_user(user.id)
    payload = McpTokenList(
        tokens=[
            McpTokenSummary(
                id=r["id"],
                name=r["name"],
                created_at=r["created_at"],
                last_used_at=r["last_used_at"],
            )
            for r in rows
        ]
    )
    return jsonify(payload.model_dump())


@bp.post("")
@login_required
def create_token():
    user = current_user()
    assert user is not None
    req = parse_body(CreateMcpTokenRequest, request.get_json(silent=True))
    try:
        token_id, raw = tokens_repo.create(user.id, req.name)
    except ValueError as exc:
        return error(str(exc), 400)
    rows = tokens_repo.list_for_user(user.id)
    me = next((r for r in rows if r["id"] == token_id), None)
    assert me is not None
    return (
        jsonify(
            CreatedMcpToken(
                id=me["id"],
                name=me["name"],
                created_at=me["created_at"],
                token=raw,
            ).model_dump()
        ),
        201,
    )


@bp.delete("/<token_id>")
@login_required
def revoke_token(token_id: str):
    user = current_user()
    assert user is not None
    if not tokens_repo.revoke(token_id, user.id):
        return error("not found", 404)
    return "", 204
