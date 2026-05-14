"""Auth surface. Session-cookie based.

Two ``AUTH_MODE`` values are supported:

  * ``basic`` — email + password, bcrypt-hashed in the users table
  * ``oidc``  — authorization-code flow via ``authlib`` against a
    configured IdP

In both modes the active session is identified by a signed
``Starlette SessionMiddleware`` cookie keyed by user id. Per-resource
permissioning for wiki pages goes through :func:`require_can`
(below), which delegates to ``app.wiki.acl``.

:data:`current_user_ctx` is the ContextVar that carries the active
user across non-HTTP code paths (workers, agent tools dispatched from
a chat loop). FastAPI's ``CurrentUserMiddleware`` binds it for every
request; worker tasks bind it via :func:`set_current_user`. Reading
the active principal anywhere — ACL checks, agent activity
attribution, trigger ``actor`` fields — goes through
:func:`current_user`.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Generator

from pydantic import BaseModel

from app.auth import users as users_repo


class User(BaseModel):
    id: str
    email: str
    name: str | None = None
    is_admin: bool = False


class PermissionDenied(Exception):
    """Raised when a wiki path access check fails. The FastAPI error
    handler in ``app.main`` translates this into a 403."""

    def __init__(self, message: str = "forbidden") -> None:
        super().__init__(message)
        self.message = message


class UserMissingError(Exception):
    """Resolving a stored ``user_id`` (e.g. from an ``mcp_jobs`` row)
    found no matching user row. Background tasks raise this and mark
    themselves failed rather than silently degrading to anonymous."""

    def __init__(self, user_id: str) -> None:
        super().__init__(f"user {user_id!r} not found")
        self.user_id = user_id


def load_user(user_id: str) -> User:
    """Resolve a stored ``user_id`` to a ``User`` value object. Raises
    :class:`UserMissingError` if the row has been deleted. Used by
    worker tasks (``app.tasks.wiki_update``) to reconstitute the
    principal before binding it via :func:`set_current_user`."""
    row = users_repo.get_by_id(user_id)
    if row is None:
        raise UserMissingError(user_id)
    return User(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        is_admin=bool(row["is_admin"]),
    )


# ContextVar seam for the active user. FastAPI's ``CurrentUserMiddleware``
# sets it from the session cookie on every request; worker tasks set it
# via :func:`set_current_user`. Reads via :func:`current_user` cascade
# through the ContextVar only — no Flask fallback.
current_user_ctx: ContextVar[User | None] = ContextVar("current_user", default=None)


@contextmanager
def set_current_user(user: User | None) -> Generator[None, None, None]:
    """Bind ``user`` as the active principal for the enclosed block.

    Used by background tasks and any code that wants downstream calls
    reading :func:`current_user` to see the request's user without
    threading a parameter. Restores the prior value on exit."""
    token = current_user_ctx.set(user)
    try:
        yield
    finally:
        current_user_ctx.reset(token)


def current_user() -> User | None:
    """Return the active user, or ``None`` for an unauthenticated /
    background-task-without-binding caller."""
    return current_user_ctx.get()


def require_can(action: str, path: str, user: User | None = None) -> None:
    """Raise :class:`PermissionDenied` if the given user lacks
    ``action`` on ``path``. ``action`` is ``"read"`` or ``"write"``.

    ``user`` defaults to :func:`current_user` so call sites that don't
    yet thread a user explicitly still work; FastAPI routes pass it
    explicitly from the ``Depends(require_user)`` resolution. Admins
    always pass. Unauthenticated callers (``user is None``) get the
    same treatment as any principal without grants. Imported lazily
    so ``app.wiki.acl`` can depend on ``app.auth`` without a cycle.
    """
    from app.wiki import acl as _acl

    if user is None:
        user = current_user()
    user_id = user.id if user is not None else None
    is_admin = bool(user is not None and user.is_admin)
    if not _acl.can(user_id, is_admin, action, path):
        raise PermissionDenied(f"forbidden: {action} on {path}")
