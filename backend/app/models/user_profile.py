"""HTTP shapes for /api/user/profile — identity fields stored on the
``users`` row itself (currently just display name).

Settings (theme, timezone, …) live in ``app/models/user_settings.py``.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class UserProfileUpdate(BaseModel):
    """Update mutable identity fields on the user row.

    ``name`` is always required in the body; an empty string clears it
    (stored as ``NULL``).
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=200)
