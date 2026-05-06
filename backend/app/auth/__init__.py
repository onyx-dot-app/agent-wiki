"""Auth surface. ``current_user`` is the canonical accessor used by API routes.

v0 supports basic auth and OIDC, no groups, no RBAC. Permissioning is
intentionally out of scope — anything authenticated can read or write.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from typing import Callable

from flask import g, jsonify, request

from app.config import CONFIG


@dataclass
class User:
    id: str
    email: str
    name: str | None = None


def current_user() -> User | None:
    return getattr(g, "user", None)


def login_required(fn: Callable) -> Callable:
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if CONFIG.auth_mode == "basic":
            from app.auth.basic import authenticate_basic
            user = authenticate_basic(request)
        else:
            from app.auth.oidc import authenticate_oidc
            user = authenticate_oidc(request)
        if user is None:
            return jsonify(error="unauthorized"), 401
        g.user = user
        return fn(*args, **kwargs)
    return wrapper
