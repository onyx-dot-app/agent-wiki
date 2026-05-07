"""Auth endpoints — signup / login / logout / me / OIDC callback."""
from __future__ import annotations

import sqlite3

from flask import Blueprint, jsonify, request, session

from app.auth import User, current_user, login_required, users as users_repo
from app.auth.basic import authenticate
from app.auth.whitelist import is_allowed, is_open
from app.config import CONFIG

bp = Blueprint("auth", __name__)


def _start_session(user: User) -> None:
    session.clear()
    session["user_id"] = user.id
    session.permanent = True


def _user_payload(user: User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "is_admin": user.is_admin}


@bp.post("/signup")
def signup():
    if CONFIG.auth_mode != "basic":
        return jsonify(error="signup disabled"), 400
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    name = (body.get("name") or "").strip() or None
    if not email or not password:
        return jsonify(error="email and password required"), 400
    if len(password) < 8:
        return jsonify(error="password must be at least 8 characters"), 400
    if not is_allowed(email):
        return jsonify(error="email not allowed"), 403
    if users_repo.get_by_email(email) is not None:
        return jsonify(error="account already exists"), 409
    try:
        user_id = users_repo.create(email=email, password=password, name=name)
    except sqlite3.IntegrityError:
        return jsonify(error="account already exists"), 409
    row = users_repo.get_by_id(user_id)
    assert row is not None
    user = User(id=row["id"], email=row["email"], name=row["name"], is_admin=bool(row["is_admin"]))
    _start_session(user)
    return jsonify(_user_payload(user)), 201


@bp.post("/login")
def login():
    if CONFIG.auth_mode != "basic":
        return jsonify(error="basic auth disabled"), 400
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    if not email or not password:
        return jsonify(error="email and password required"), 400
    user = authenticate(email, password)
    if user is None:
        return jsonify(error="invalid credentials"), 401
    _start_session(user)
    return jsonify(_user_payload(user))


@bp.post("/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@bp.get("/oidc/callback")
def oidc_callback():
    # TODO: OIDC redirect handler (authlib).
    raise NotImplementedError


@bp.get("/me")
@login_required
def me():
    user = current_user()
    assert user is not None  # login_required guarantees this
    return jsonify(_user_payload(user))


@bp.get("/config")
def auth_config():
    """Public — frontend uses this to know whether to show the signup form."""
    return jsonify(mode=CONFIG.auth_mode, signup_open=is_open())
