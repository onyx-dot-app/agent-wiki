"""HTTP shapes for /api/auth.

These cover password-flow signup/login plus the public ``/auth/config``
endpoint. OIDC redirects are handled by authlib and don't need request
bodies.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=8)
    name: str | None = None


class LoginRequest(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)


class AuthSession(BaseModel):
    """Logged-in user payload returned by signup/login/me."""

    id: str
    email: str
    name: str | None
    is_admin: bool


class AuthConfig(BaseModel):
    """Public auth configuration — used by the frontend to know whether to
    show the signup form."""

    mode: str       # "basic" | "oidc"
    signup_open: bool


class OkResponse(BaseModel):
    ok: bool = True
