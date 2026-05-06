"""OIDC auth via authlib. v0 stub — issuer/client id/secret read from CONFIG."""
from __future__ import annotations

from flask import Request

from app.auth import User


def authenticate_oidc(request: Request) -> User | None:
    # TODO: validate session cookie / bearer token against the OIDC issuer
    # (use authlib.integrations.flask_client.OAuth), upsert the user, return User.
    raise NotImplementedError
