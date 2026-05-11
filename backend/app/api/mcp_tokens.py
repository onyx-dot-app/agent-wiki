"""FastAPI port of ``app/api/mcp_tokens.py`` (Phase 2)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.auth import User
from app.auth import mcp_tokens as tokens_repo
from app.auth.deps import require_user
from app.models.mcp import (
    CreateMcpTokenRequest,
    CreatedMcpToken,
    McpTokenList,
    McpTokenSummary,
)

router = APIRouter()


@router.get("", response_model=McpTokenList)
def list_tokens(user: User = Depends(require_user)) -> McpTokenList:
    rows = tokens_repo.list_for_user(user.id)
    return McpTokenList(
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


@router.post(
    "", response_model=CreatedMcpToken, status_code=status.HTTP_201_CREATED
)
def create_token(
    req: CreateMcpTokenRequest, user: User = Depends(require_user)
) -> CreatedMcpToken:
    try:
        token_id, raw = tokens_repo.create(user.id, req.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rows = tokens_repo.list_for_user(user.id)
    me = next((r for r in rows if r["id"] == token_id), None)
    assert me is not None
    return CreatedMcpToken(
        id=me["id"],
        name=me["name"],
        created_at=me["created_at"],
        token=raw,
    )


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_token(token_id: str, user: User = Depends(require_user)) -> Response:
    if not tokens_repo.revoke(token_id, user.id):
        raise HTTPException(status_code=404, detail="not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
