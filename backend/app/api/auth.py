"""Auth endpoints — signup / login / logout / me / OIDC login + callback."""
from __future__ import annotations

import logging
import sqlite3

from flask import Blueprint, current_app, jsonify, redirect, request, session, url_for

from app.auth import User, current_user, login_required, users as users_repo
from app.auth.basic import authenticate
from app.auth.oidc import CLIENT_NAME as OIDC_CLIENT_NAME, upsert_oidc_user
from app.auth.whitelist import is_allowed, is_open
from app.config import CONFIG

bp = Blueprint("auth", __name__)
log = logging.getLogger(__name__)


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
        log.warning("signup race: account already exists for %s", email, exc_info=True)
        return jsonify(error="account already exists"), 409
    row = users_repo.get_by_id(user_id)
    assert row is not None
    user = User(id=row["id"], email=row["email"], name=row["name"], is_admin=bool(row["is_admin"]))
    _start_session(user)
    log.info("signup: user %s (%s) is_admin=%s", user.id, user.email, user.is_admin)
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
        log.warning("login failed for %s", email)
        return jsonify(error="invalid credentials"), 401
    _start_session(user)
    log.info("login: user %s (%s)", user.id, user.email)
    return jsonify(_user_payload(user))


@bp.post("/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


def _oidc_client():
    """Return the registered OIDC client, or None if OIDC isn't configured."""
    oauth = current_app.extensions.get("authlib.integrations.flask_client")
    if oauth is None:
        return None
    return oauth.create_client(OIDC_CLIENT_NAME)


@bp.get("/oidc/login")
def oidc_login():
    """Kick off the OIDC authorization-code flow."""
    if CONFIG.auth_mode != "oidc":
        return jsonify(error="oidc disabled"), 400
    client = _oidc_client()
    if client is None:
        return jsonify(error="oidc not configured"), 503
    # Use the explicit redirect URI when set (matches what's registered with
    # the IdP); otherwise reconstruct from the request so dev/local also works.
    redirect_uri = CONFIG.oidc_redirect_uri or url_for("auth.oidc_callback", _external=True)
    return client.authorize_redirect(redirect_uri)


@bp.get("/oidc/callback")
def oidc_callback():
    """OIDC redirect handler — exchanges code for token, upserts user, starts session."""
    if CONFIG.auth_mode != "oidc":
        return jsonify(error="oidc disabled"), 400
    client = _oidc_client()
    if client is None:
        return jsonify(error="oidc not configured"), 503
    try:
        token = client.authorize_access_token()
    except Exception:
        log.exception("oidc: failed to exchange authorization code")
        return redirect("/login?error=oidc_exchange_failed")

    userinfo = token.get("userinfo")
    if userinfo is None:
        try:
            userinfo = client.userinfo(token=token)
        except Exception:
            log.exception("oidc: failed to fetch userinfo")
            return redirect("/login?error=oidc_userinfo_failed")

    email = (userinfo.get("email") or "").strip().lower()
    if not email:
        log.warning("oidc: userinfo missing email; payload keys=%s", list(userinfo.keys()))
        return redirect("/login?error=oidc_no_email")
    if userinfo.get("email_verified") is False:
        log.warning("oidc: email not verified for %s", email)
        return redirect("/login?error=oidc_email_unverified")
    if not is_allowed(email):
        log.info("oidc: email %s not in allow list", email)
        return redirect("/login?error=oidc_email_not_allowed")

    name = userinfo.get("name") or None
    user_id = upsert_oidc_user(email=email, name=name)
    row = users_repo.get_by_id(user_id)
    assert row is not None
    user = User(id=row["id"], email=row["email"], name=row["name"], is_admin=bool(row["is_admin"]))
    _start_session(user)
    log.info("oidc login: user %s (%s)", user.id, user.email)
    return redirect("/")


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
