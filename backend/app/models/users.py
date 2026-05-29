"""Pydantic schemas for the public user-lookup API.

Used by ``app/api/users.py:search_users`` to feed the share / transfer
ownership typeaheads. Deliberately minimal — any signed-in user can call
the search, so only public-safe fields are exposed (no password hash,
settings, or admin flag).
"""

from __future__ import annotations

from pydantic import BaseModel


class UserLite(BaseModel):
    id: str
    email: str
    name: str | None


class UserSearchResponse(BaseModel):
    users: list[UserLite]
