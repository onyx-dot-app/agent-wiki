"""Email + password verification."""
from __future__ import annotations

from app.auth import User, users as users_repo
from app.auth.passwords import verify_password


def authenticate(email: str, password: str) -> User | None:
    row = users_repo.get_by_email(email)
    if row is None or not row["password_hash"]:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return User(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        is_admin=bool(row["is_admin"]),
    )
