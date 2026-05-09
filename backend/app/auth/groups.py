"""Group repo — CRUD + membership.

Free functions over the ``Group`` and ``GroupMember`` ORM models, same
shape as ``app.auth.users``. All return plain dicts so callers don't
depend on the ORM.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import delete, select

from app.db.models import Group, GroupMember, User
from app.db.session import session

log = logging.getLogger(__name__)


class GroupNameTakenError(Exception):
    pass


class GroupNotFoundError(Exception):
    pass


class UserNotFoundError(Exception):
    pass


def _to_dict(g: Group) -> dict[str, Any]:
    return {
        "id": g.id,
        "name": g.name,
        "description": g.description,
        "created_by_user_id": g.created_by_user_id,
        "created_at": g.created_at,
    }


def create(name: str, description: str | None, created_by_user_id: str) -> str:
    """Create a group. Raises ``GroupNameTakenError`` if the name is in use."""
    name = name.strip()
    if not name:
        raise ValueError("group name required")
    gid = f"grp_{uuid.uuid4().hex[:12]}"
    with session() as s:
        existing = s.scalar(select(Group).where(Group.name == name))
        if existing is not None:
            raise GroupNameTakenError(f"group name already in use: {name!r}")
        s.add(
            Group(
                id=gid,
                name=name,
                description=description,
                created_by_user_id=created_by_user_id,
            )
        )
    log.info("group created id=%s name=%s by=%s", gid, name, created_by_user_id)
    return gid


def get(group_id: str) -> dict[str, Any] | None:
    with session() as s:
        g = s.get(Group, group_id)
        return _to_dict(g) if g else None


def get_by_name(name: str) -> dict[str, Any] | None:
    with session() as s:
        g = s.scalar(select(Group).where(Group.name == name.strip()))
        return _to_dict(g) if g else None


def delete_group(group_id: str) -> None:
    with session() as s:
        g = s.get(Group, group_id)
        if g is not None:
            s.delete(g)


def list_all() -> list[dict[str, Any]]:
    with session() as s:
        rows = s.scalars(select(Group).order_by(Group.name.asc())).all()
        return [_to_dict(g) for g in rows]


def list_for_user(user_id: str) -> list[dict[str, Any]]:
    """Groups the user is a member of, ordered by name."""
    stmt = (
        select(Group)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(GroupMember.user_id == user_id)
        .order_by(Group.name.asc())
    )
    with session() as s:
        rows = s.scalars(stmt).all()
        return [_to_dict(g) for g in rows]


def member_ids(group_id: str) -> list[str]:
    """User ids belonging to ``group_id``."""
    with session() as s:
        return list(
            s.scalars(
                select(GroupMember.user_id).where(GroupMember.group_id == group_id)
            ).all()
        )


def members(group_id: str) -> list[dict[str, Any]]:
    """Members of a group, joined onto ``users`` for display."""
    stmt = (
        select(User)
        .join(GroupMember, GroupMember.user_id == User.id)
        .where(GroupMember.group_id == group_id)
        .order_by(User.email.asc())
    )
    with session() as s:
        rows = s.scalars(stmt).all()
        return [
            {"id": u.id, "email": u.email, "name": u.name, "is_admin": u.is_admin}
            for u in rows
        ]


def group_ids_for_user(user_id: str) -> list[str]:
    """All group ids the user belongs to. Used by the ACL resolver to
    expand a user into the set of group principals it satisfies."""
    with session() as s:
        return list(
            s.scalars(
                select(GroupMember.group_id).where(GroupMember.user_id == user_id)
            ).all()
        )


def add_member(group_id: str, user_id: str) -> None:
    """Add a user to a group. Idempotent — re-adding is a no-op.

    Raises ``GroupNotFoundError`` / ``UserNotFoundError`` if either side
    doesn't exist.
    """
    with session() as s:
        if s.get(Group, group_id) is None:
            raise GroupNotFoundError(group_id)
        if s.get(User, user_id) is None:
            raise UserNotFoundError(user_id)
        existing = s.get(GroupMember, (group_id, user_id))
        if existing is not None:
            return
        s.add(GroupMember(group_id=group_id, user_id=user_id))
    log.info("group member added group=%s user=%s", group_id, user_id)


def remove_member(group_id: str, user_id: str) -> None:
    with session() as s:
        s.execute(
            delete(GroupMember).where(
                GroupMember.group_id == group_id,
                GroupMember.user_id == user_id,
            )
        )
