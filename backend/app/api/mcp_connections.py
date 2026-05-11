"""FastAPI port of ``app/api/mcp_connections.py`` (Phase 2). All v0 stubs."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import User
from app.auth.deps import require_user

router = APIRouter()


@router.get("")
def list_connections(_user: User = Depends(require_user)) -> None:
    raise NotImplementedError


@router.post("")
def create_connection(_user: User = Depends(require_user)) -> None:
    # body: {name, transport, config}
    raise NotImplementedError


@router.delete("/{conn_id}")
def delete_connection(conn_id: str, _user: User = Depends(require_user)) -> None:
    raise NotImplementedError
