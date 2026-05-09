"""User repo — SQLAlchemy ORM. Free functions over ``User`` model."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, select

from app.auth.passwords import hash_password
from app.db.models import User
from app.db.session import session

log = logging.getLogger(__name__)


def _to_dict(u: User) -> dict[str, Any]:
    return {
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "password_hash": u.password_hash,
        "is_admin": u.is_admin,
        "created_at": u.created_at,
    }


def get_by_email(email: str) -> dict[str, Any] | None:
    with session() as s:
        u = s.scalar(select(User).where(User.email == email.lower()))
        return _to_dict(u) if u else None


def get_by_id(user_id: str) -> dict[str, Any] | None:
    with session() as s:
        u = s.get(User, user_id)
        return _to_dict(u) if u else None


def count() -> int:
    with session() as s:
        return s.scalar(select(func.count()).select_from(User)) or 0


def list_all() -> list[dict[str, Any]]:
    with session() as s:
        users = s.scalars(select(User).order_by(User.created_at.asc())).all()
        return [_to_dict(u) for u in users]


def create(email: str, password: str, name: str | None = None) -> str:
    """Create a user. The very first user is auto-promoted to admin."""
    user_id = str(uuid.uuid4())
    with session() as s:
        existing_count = s.scalar(select(func.count()).select_from(User)) or 0
        is_admin = existing_count == 0
        s.add(
            User(
                id=user_id,
                email=email.lower(),
                name=name,
                password_hash=hash_password(password),
                is_admin=is_admin,
            )
        )
    log.info("user created id=%s email=%s is_admin=%s", user_id, email.lower(), is_admin)
    return user_id


def set_admin(user_id: str, is_admin: bool) -> None:
    with session() as s:
        u = s.get(User, user_id)
        if u is not None:
            u.is_admin = is_admin


def admin_count() -> int:
    with session() as s:
        return s.scalar(
            select(func.count()).select_from(User).where(User.is_admin.is_(True))
        ) or 0


def delete(user_id: str) -> None:
    with session() as s:
        u = s.get(User, user_id)
        if u is not None:
            s.delete(u)
