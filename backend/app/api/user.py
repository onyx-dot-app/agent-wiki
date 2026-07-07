"""FastAPI port of ``app/api/user.py`` (Phase 2)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError

from app.auth import User
from app.auth import users as users_repo
from app.auth.deps import require_user
from pydantic import BaseModel, Field

from app.auth.passwords import verify_password
from app.models._helpers import RequestError
from app.models.auth import AuthSession
from app.models.user_profile import UserProfileUpdate
from app.models.user_settings import UserSettings, UserSettingsUpdate

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/settings", response_model=UserSettings)
def get_settings(user: User = Depends(require_user)) -> UserSettings:
    settings = users_repo.get_settings(user.id)
    if settings is None:
        raise HTTPException(status_code=404, detail="not found")
    return UserSettings.model_validate(settings)


@router.put("/settings", response_model=UserSettings)
def put_settings(
    req: UserSettingsUpdate, user: User = Depends(require_user)
) -> UserSettings:
    partial = req.non_null()
    try:
        updated = users_repo.update_settings(user.id, partial)
    except ValidationError as exc:
        # The partial parser only checks per-field types; merged-shape
        # validation (e.g. ``timezone`` against zoneinfo) runs in the repo.
        # Surface those failures as 400, not 500.
        err = exc.errors()[0]
        loc = ".".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "invalid value")
        raise RequestError(f"{loc}: {msg}" if loc else msg) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="not found")
    log.info("user %s updated settings keys=%s", user.id, sorted(partial.keys()))
    return UserSettings.model_validate(updated)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)


@router.put("/password", status_code=status.HTTP_204_NO_CONTENT)
def put_password(
    request: Request,
    req: ChangePasswordRequest,
    user: User = Depends(require_user),
) -> None:
    row = users_repo.get_by_id(user.id)
    if row is None or not verify_password(
        req.current_password, str(row.get("password_hash") or "")
    ):
        raise RequestError("current password is incorrect")
    new_epoch = users_repo.set_password(user.id, req.new_password)
    if new_epoch is None:
        # The row vanished between the check and the write.
        raise HTTPException(status_code=404, detail="not found")
    # Other sessions die with the old epoch; the changer's stays live.
    request.session["session_epoch"] = new_epoch
    log.info("user %s changed password", user.id)


@router.put("/profile", response_model=AuthSession)
def put_profile(
    req: UserProfileUpdate, user: User = Depends(require_user)
) -> AuthSession:
    name = req.name.strip() or None
    updated = users_repo.update_name(user.id, name)
    if updated is None:
        raise HTTPException(status_code=404, detail="not found")
    log.info("user %s updated profile name", user.id)
    return AuthSession(
        id=updated["id"],
        email=updated["email"],
        name=updated["name"],
        is_admin=updated["is_admin"],
        settings=UserSettings.model_validate(updated["settings"]),
    )
