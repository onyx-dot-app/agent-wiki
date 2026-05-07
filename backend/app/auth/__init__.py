"""Auth surface. Session-cookie based.

v0 supports two AUTH_MODEs:
  * ``basic`` — email + password, bcrypt-hashed in the users table
  * ``oidc``  — TODO

In both modes the active session is identified by a Flask server-side session
cookie keyed by user id. Permissioning is intentionally minimal — anything
authenticated can read or write, except for endpoints behind ``admin_required``.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from typing import Callable

from flask import g, jsonify, session

from app.auth import users as users_repo


@dataclass
class User:
    id: str
    email: str
    name: str | None = None
    is_admin: bool = False


def current_user() -> User | None:
    user = getattr(g, "user", None)
    if user is not None:
        return user
    user_id = session.get("user_id")
    if not user_id:
        return None
    row = users_repo.get_by_id(user_id)
    if row is None:
        return None
    g.user = User(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        is_admin=bool(row["is_admin"]),
    )
    return g.user


def login_required(fn: Callable) -> Callable:
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            return jsonify(error="unauthorized"), 401
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn: Callable) -> Callable:
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            return jsonify(error="unauthorized"), 401
        if not user.is_admin:
            return jsonify(error="forbidden"), 403
        return fn(*args, **kwargs)
    return wrapper
