"""FastAPI port of ``app/api/users.py`` (Phase 2). All v0 stubs."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import User
from app.auth import users as users_repo
from app.auth.deps import require_user
from app.models.users import UserLite, UserSearchResponse

router = APIRouter()


@router.get("")
def list_users(_user: User = Depends(require_user)) -> None:
    raise NotImplementedError


@router.get("/search", response_model=UserSearchResponse)
def search_users(
    q: str = "",
    limit: int = 20,
    _user: User = Depends(require_user),
) -> UserSearchResponse:
    """Typeahead lookup for the share / transfer dialogs. Any signed-in
    user may search so they can grant page access to colleagues by name
    or email. Declared before ``/{user_id}`` so the literal path wins."""
    limit = max(1, min(limit, 50))
    rows = users_repo.search(q, limit)
    return UserSearchResponse(users=[UserLite(**r) for r in rows])


@router.post("")
def create_user(_user: User = Depends(require_user)) -> None:
    raise NotImplementedError


@router.get("/{user_id}")
def get_user(user_id: str, _user: User = Depends(require_user)) -> None:
    raise NotImplementedError
