"""Users CRUD. v0 stubs."""
from __future__ import annotations

from flask import Blueprint

from app.auth import login_required

bp = Blueprint("users", __name__)


@bp.get("")
@login_required
def list_users():
    raise NotImplementedError


@bp.post("")
@login_required
def create_user():
    raise NotImplementedError


@bp.get("/<user_id>")
@login_required
def get_user(user_id: str):
    raise NotImplementedError
