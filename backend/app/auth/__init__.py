"""Auth surface. Session-cookie based.

v0 supports two AUTH_MODEs:
  * ``basic`` — email + password, bcrypt-hashed in the users table
  * ``oidc``  — TODO

In both modes the active session is identified by a Flask server-side session
cookie keyed by user id. Permissioning is intentionally minimal — anything
authenticated can read or write, except for endpoints behind ``admin_required``.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar, cast

from flask import g, jsonify, session
from pydantic import BaseModel

from app.auth import users as users_repo

F = TypeVar("F", bound=Callable[..., Any])


class User(BaseModel):
    id: str
    email: str
    name: str | None = None
    is_admin: bool = False


def current_user() -> User | None:
    cached = cast("User | None", getattr(g, "user", None))
    if cached is not None:
        return cached
    user_id = session.get("user_id")
    if not user_id:
        return None
    row = users_repo.get_by_id(user_id)
    if row is None:
        return None
    user = User(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        is_admin=bool(row["is_admin"]),
    )
    g.user = user
    return user


def login_required(fn: F) -> F:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if current_user() is None:
            return jsonify(error="unauthorized"), 401
        return fn(*args, **kwargs)
    return cast(F, wrapper)


def admin_required(fn: F) -> F:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        user = current_user()
        if user is None:
            return jsonify(error="unauthorized"), 401
        if not user.is_admin:
            return jsonify(error="forbidden"), 403
        return fn(*args, **kwargs)
    return cast(F, wrapper)
