"""Auth surface. Session-cookie based.

v0 supports two AUTH_MODEs:
  * ``basic`` — email + password, bcrypt-hashed in the users table
  * ``oidc``  — TODO

In both modes the active session is identified by a Flask server-side session
cookie keyed by user id. Per-resource permissioning for wiki pages goes
through ``require_can`` (below), which delegates to ``app.wiki.acl``;
``admin_required`` still gates the admin-only endpoints.
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


class PermissionDenied(Exception):
    """Raised when a wiki path access check fails. The Flask error
    handler (``app.main``) translates this into a 403."""

    def __init__(self, message: str = "forbidden") -> None:
        super().__init__(message)
        self.message = message


def current_user() -> User | None:
    # ``g``/``session`` raise ``RuntimeError`` outside an app/request
    # context. Treat that as "no current user" — agent tools dispatched
    # from a test or background-task harness (no Flask request) get the
    # anonymous principal, and the resolver applies the same rules
    # (``everyone`` grants only) it would for a logged-out caller.
    try:
        cached = cast("User | None", getattr(g, "user", None))
    except RuntimeError:
        return None
    if cached is not None:
        return cached
    try:
        user_id = session.get("user_id")
    except RuntimeError:
        return None
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


def require_can(action: str, path: str) -> None:
    """Raise ``PermissionDenied`` if the current user lacks ``action`` on
    ``path``. ``action`` is ``"read"`` or ``"write"``.

    Callers are expected to be ``@login_required`` already — the helper
    treats an unauthenticated caller the same as any other principal
    without grants. Admins always pass. Imported lazily so
    ``app.wiki.acl`` can depend on ``app.auth`` without a cycle.
    """
    from app.wiki import acl as _acl

    user = current_user()
    user_id = user.id if user is not None else None
    is_admin = bool(user is not None and user.is_admin)
    if not _acl.can(user_id, is_admin, action, path):
        raise PermissionDenied(f"forbidden: {action} on {path}")
