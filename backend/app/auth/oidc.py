"""OIDC auth via authlib's Flask integration.

Wired in :func:`init_oauth` from ``app/main.py`` when ``AUTH_MODE=oidc``.
The login + callback endpoints in ``app/api/auth.py`` use the registered
client to drive an OIDC authorization-code flow against the configured
issuer (Google, in practice).
"""
from __future__ import annotations

import logging
import secrets

from authlib.integrations.flask_client import OAuth
from flask import Flask

from app.auth import users as users_repo
from app.config import CONFIG

log = logging.getLogger(__name__)

# Registered client name. Used by the API layer as ``oauth.create_client("oidc")``.
CLIENT_NAME = "oidc"


def init_oauth(app: Flask) -> OAuth:
    """Create and register an OAuth client on the Flask app.

    Always returns the OAuth instance so ``app.extensions["authlib.integrations.flask_client"]``
    is populated; only registers the OIDC client when ``AUTH_MODE=oidc`` and
    issuer/client credentials are configured.
    """
    oauth = OAuth(app)

    if CONFIG.auth_mode != "oidc":
        return oauth

    if not (CONFIG.oidc_issuer and CONFIG.oidc_client_id and CONFIG.oidc_client_secret):
        log.warning("AUTH_MODE=oidc but issuer/client credentials are not fully set; OIDC disabled")
        return oauth

    issuer = CONFIG.oidc_issuer.rstrip("/")
    oauth.register(
        name=CLIENT_NAME,
        server_metadata_url=f"{issuer}/.well-known/openid-configuration",
        client_id=CONFIG.oidc_client_id,
        client_secret=CONFIG.oidc_client_secret,
        client_kwargs={"scope": "openid email profile"},
    )
    log.info("OIDC client registered (issuer=%s)", issuer)
    return oauth


def upsert_oidc_user(email: str, name: str | None) -> str:
    """Find or create a user by email for an OIDC sign-in.

    Returns the user's id. First user created is auto-promoted to admin
    (same convention as ``users.create``). Subsequent OIDC sign-ins for an
    existing email are no-ops on the user row — we don't overwrite ``name``
    or ``is_admin`` to avoid surprising downgrades.
    """
    existing = users_repo.get_by_email(email)
    if existing is not None:
        return existing["id"]
    # Random password the user can never use; OIDC sign-in bypasses
    # ``authenticate``. Schema requires password_hash; storing a hash of a
    # random secret keeps the column non-null without inventing a sentinel.
    return users_repo.create(email=email, password=secrets.token_hex(32), name=name)
