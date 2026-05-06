"""Auth endpoints — login/logout/oidc-callback. v0 stubs."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

bp = Blueprint("auth", __name__)


@bp.post("/login")
def login():
    # TODO: basic auth login → set session cookie
    raise NotImplementedError


@bp.post("/logout")
def logout():
    raise NotImplementedError


@bp.get("/oidc/callback")
def oidc_callback():
    # TODO: handle OIDC redirect, exchange code, set session
    raise NotImplementedError


@bp.get("/me")
def me():
    # TODO: return the current user (or 401)
    raise NotImplementedError
