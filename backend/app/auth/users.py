"""User repo — SQLAlchemy ORM. Free functions over ``User`` model."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.orm.attributes import flag_modified

from app.auth.passwords import hash_password
from app.db.models import User
from app.db.session import session
from app.models.user_settings import UserSettings

log = logging.getLogger(__name__)


def _now() -> str:
    """UTC timestamp matching the ``YYYY-MM-DD HH:MM:SS`` column format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _to_dict(u: User) -> dict[str, Any]:
    return {
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "password_hash": u.password_hash,
        "is_admin": u.is_admin,
        "is_active": u.is_active,
        "created_at": u.created_at,
        "updated_at": u.updated_at,
        "settings": _settings_with_defaults(u.settings),
    }


def _settings_with_defaults(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Run a (possibly stale or empty) JSONB blob through ``UserSettings``
    so callers always see every field populated with the current default."""
    return UserSettings.model_validate(raw or {}).model_dump()


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


def get_many(user_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    ids = list(dict.fromkeys(user_ids))
    if not ids:
        return {}
    stmt = select(User).where(User.id.in_(ids))
    with session() as s:
        rows = s.scalars(stmt).all()
        return {u.id: _to_dict(u) for u in rows}


def search(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Minimal user lookup for the share / transfer typeaheads.

    Case-insensitive substring match on email or name. An empty query
    returns the first ``limit`` users by email so the picker can show
    suggestions before the user types. Returns only public-safe fields
    (no password hash, settings, or admin flag) since any signed-in user
    can call this to share a page.
    """
    q = (query or "").strip().lower()
    with session() as s:
        stmt = select(User)
        if q:
            like = "%" + q + "%"
            stmt = stmt.where(
                or_(
                    func.lower(User.email).like(like),
                    func.lower(User.name).like(like),
                )
            )
        stmt = stmt.order_by(User.email.asc()).limit(limit)
        return [
            {"id": u.id, "email": u.email, "name": u.name}
            for u in s.scalars(stmt).all()
        ]


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
    log.info(
        "user created id=%s email=%s is_admin=%s", user_id, email.lower(), is_admin
    )
    return user_id


def set_admin(user_id: str, is_admin: bool) -> None:
    with session() as s:
        u = s.get(User, user_id)
        if u is not None:
            u.is_admin = is_admin
            u.updated_at = _now()


def set_active(user_id: str, is_active: bool) -> None:
    with session() as s:
        u = s.get(User, user_id)
        if u is not None:
            u.is_active = is_active
            u.updated_at = _now()


def status_counts() -> dict[str, int]:
    """``{active, inactive}`` counts over real accounts (invited emails are
    counted separately, in ``app.auth.invites``)."""
    with session() as s:
        active = (
            s.scalar(
                select(func.count()).select_from(User).where(User.is_active.is_(True))
            )
            or 0
        )
        total = s.scalar(select(func.count()).select_from(User)) or 0
    return {"active": active, "inactive": total - active}


def admin_count() -> int:
    """Number of *active* admins. The last-admin guards (demote / delete /
    deactivate) rely on this — an inactive admin can't log in, so counting
    them would let the sole active admin be removed and lock everyone out."""
    with session() as s:
        return (
            s.scalar(
                select(func.count())
                .select_from(User)
                .where(User.is_admin.is_(True), User.is_active.is_(True))
            )
            or 0
        )


def delete(user_id: str) -> None:
    with session() as s:
        u = s.get(User, user_id)
        if u is not None:
            s.delete(u)


def update_name(user_id: str, name: str | None) -> dict[str, Any] | None:
    """Set the user's display name. Returns the refreshed user dict, or
    None if the user doesn't exist."""
    with session() as s:
        u = s.get(User, user_id)
        if u is None:
            return None
        u.name = name
        u.updated_at = _now()
        s.flush()
        return _to_dict(u)


def get_settings(user_id: str) -> dict[str, Any] | None:
    with session() as s:
        u = s.get(User, user_id)
        if u is None:
            return None
        return _settings_with_defaults(u.settings)


def update_settings(user_id: str, partial: dict[str, Any]) -> dict[str, Any] | None:
    """Merge ``partial`` into the user's stored settings, validate, persist.

    Returns the resulting full settings dict (defaults filled), or None
    if the user doesn't exist.
    """
    with session() as s:
        u = s.get(User, user_id)
        if u is None:
            return None
        merged = {**(u.settings or {}), **partial}
        validated = UserSettings.model_validate(merged).model_dump()
        u.settings = validated
        # JSONB stored as a dict — SQLAlchemy can't tell we mutated by
        # assignment vs. in-place; flag it explicitly so the UPDATE fires.
        flag_modified(u, "settings")
        return validated
