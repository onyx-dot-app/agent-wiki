from __future__ import annotations

from pydantic import BaseModel, EmailStr


class User(BaseModel):
    id: str
    email: EmailStr
    name: str | None = None
    created_at: str
