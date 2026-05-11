"""FastAPI port of ``app/api/users.py`` (Phase 2). All v0 stubs."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import User
from app.auth.deps import require_user

router = APIRouter()


@router.get("")
def list_users(_user: User = Depends(require_user)) -> None:
    raise NotImplementedError


@router.post("")
def create_user(_user: User = Depends(require_user)) -> None:
    raise NotImplementedError


@router.get("/{user_id}")
def get_user(user_id: str, _user: User = Depends(require_user)) -> None:
    raise NotImplementedError
