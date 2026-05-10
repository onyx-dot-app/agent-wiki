"""Per-user settings — the only endpoints under /api/user.

Sibling to /api/auth (identity) and /api/admin (org-wide config). Every
preference here is scoped to the calling user; admins don't get a
back-door view of someone else's settings.
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request
from pydantic import ValidationError

from app.auth import current_user, login_required, users as users_repo
from app.models._helpers import RequestError, error, parse_body
from app.models.auth import AuthSession
from app.models.user_profile import UserProfileUpdate
from app.models.user_settings import UserSettings, UserSettingsUpdate

bp = Blueprint("user", __name__)
log = logging.getLogger(__name__)


@bp.get("/settings")
@login_required
def get_settings():
    user = current_user()
    assert user is not None
    settings = users_repo.get_settings(user.id)
    if settings is None:
        return error("not found", 404)
    return jsonify(UserSettings.model_validate(settings).model_dump())


@bp.put("/settings")
@login_required
def put_settings():
    user = current_user()
    assert user is not None
    req = parse_body(UserSettingsUpdate, request.get_json(silent=True))
    partial = req.non_null()
    try:
        updated = users_repo.update_settings(user.id, partial)
    except ValidationError as exc:
        # The partial parser only checks per-field types; merged-shape
        # validation (e.g. ``timezone`` against zoneinfo) runs in the
        # repo. Surface those failures as 400, not 500.
        err = exc.errors()[0]
        loc = ".".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "invalid value")
        raise RequestError(f"{loc}: {msg}" if loc else msg) from exc
    if updated is None:
        return error("not found", 404)
    log.info("user %s updated settings keys=%s", user.id, sorted(partial.keys()))
    return jsonify(UserSettings.model_validate(updated).model_dump())


@bp.put("/profile")
@login_required
def put_profile():
    user = current_user()
    assert user is not None
    req = parse_body(UserProfileUpdate, request.get_json(silent=True))
    name = req.name.strip() or None
    updated = users_repo.update_name(user.id, name)
    if updated is None:
        return error("not found", 404)
    log.info("user %s updated profile name", user.id)
    return jsonify(
        AuthSession(
            id=updated["id"],
            email=updated["email"],
            name=updated["name"],
            is_admin=updated["is_admin"],
            settings=UserSettings.model_validate(updated["settings"]),
        ).model_dump()
    )
