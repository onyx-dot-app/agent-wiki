"""Invited-email repo — emails allowed to sign up but without an account yet.

An invite is consumed (the row deleted) when the email signs up; see
``app.auth.users.create``'s callers in ``app/api/auth.py``. Invited emails
also pass the signup allowlist (``app.auth.whitelist``).
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.db.models import InvitedUser, User
from app.db.session import session

log = logging.getLogger(__name__)


def list_emails() -> list[str]:
    """Pending invites — invited emails that have NOT signed up yet."""
    with session() as s:
        invited = set(s.scalars(select(InvitedUser.email)).all())
        if not invited:
            return []
        signed_up = set(s.scalars(select(User.email)).all())
        return sorted(invited - signed_up)


def count() -> int:
    return len(list_emails())


def is_invited(email: str) -> bool:
    with session() as s:
        return s.get(InvitedUser, email.strip().lower()) is not None


def add(emails: list[str], invited_by_user_id: str | None) -> list[str]:
    """Insert any new invited emails (skips ones that already have an account
    or are already invited). Returns the emails actually added."""
    cleaned = [e.strip().lower() for e in emails if e.strip()]
    if not cleaned:
        return []
    added: list[str] = []
    with session() as s:
        existing_accounts = set(s.scalars(select(User.email)).all())
        existing_invites = set(s.scalars(select(InvitedUser.email)).all())
        for email in dict.fromkeys(cleaned):
            if email in existing_accounts or email in existing_invites:
                continue
            s.add(InvitedUser(email=email, invited_by_user_id=invited_by_user_id))
            added.append(email)
    if added:
        log.info("invited %d email(s): %s", len(added), ", ".join(added))
    return added


def remove(email: str) -> None:
    with session() as s:
        row = s.get(InvitedUser, email.strip().lower())
        if row is not None:
            s.delete(row)
