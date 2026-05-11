"""OIDC auth via authlib's Starlette integration.

The configured client is built lazily at first use; ``app.main``
doesn't have to thread it through the FastAPI factory. The login +
callback endpoints in ``app/api/auth.py`` drive an OIDC
authorization-code flow against the configured issuer.

The OAuth state (PKCE verifier + nonce) is round-tripped via
Starlette's ``SessionMiddleware`` — the same session cookie that
carries ``user_id`` after login.
"""
from __future__ import annotations

import logging
import secrets
from typing import cast

from authlib.integrations.starlette_client import OAuth
from authlib.integrations.starlette_client import StarletteOAuth2App

from app.auth import users as users_repo
from app.config import CONFIG

log = logging.getLogger(__name__)

# Registered client name. ``client()`` resolves it back to the
# ``StarletteOAuth2App`` instance.
CLIENT_NAME = "oidc"

_oauth: OAuth | None = None


def _build_oauth() -> OAuth | None:
    """Construct and register the OAuth client if OIDC is configured.

    Returns ``None`` when ``AUTH_MODE != oidc`` or credentials are
    missing — callers (``/oidc/login`` / ``/oidc/callback``) translate
    that into a 503 so the surface is the same shape as before.
    """
    if CONFIG.auth_mode != "oidc":
        return None
    if not (CONFIG.oidc_issuer and CONFIG.oidc_client_id and CONFIG.oidc_client_secret):
        log.warning(
            "AUTH_MODE=oidc but issuer/client credentials are not fully set; OIDC disabled",
        )
        return None

    issuer = CONFIG.oidc_issuer.rstrip("/")
    oauth = OAuth()
    oauth.register(  # pyright: ignore[reportUnknownMemberType]
        name=CLIENT_NAME,
        server_metadata_url=f"{issuer}/.well-known/openid-configuration",
        client_id=CONFIG.oidc_client_id,
        client_secret=CONFIG.oidc_client_secret,
        client_kwargs={"scope": "openid email profile"},
    )
    log.info("OIDC client registered (issuer=%s)", issuer)
    return oauth


def client() -> StarletteOAuth2App | None:
    """Return the registered OIDC client, lazily constructing the OAuth
    registry on first call. Returns ``None`` when OIDC isn't configured."""
    global _oauth
    if _oauth is None:
        _oauth = _build_oauth()
    if _oauth is None:
        return None
    # ``create_client`` returns the registered ``StarletteOAuth2App``.
    return cast(
        StarletteOAuth2App | None,
        _oauth.create_client(CLIENT_NAME),  # pyright: ignore[reportUnknownMemberType]
    )


def upsert_oidc_user(email: str, name: str | None) -> str:
    """Find or create a user by email for an OIDC sign-in.

    Returns the user's id. First user created is auto-promoted to admin
    (same convention as ``users.create``). Subsequent OIDC sign-ins for
    an existing email are no-ops on the user row — we don't overwrite
    ``name`` or ``is_admin`` to avoid surprising downgrades.
    """
    existing = users_repo.get_by_email(email)
    if existing is not None:
        return existing["id"]
    # Random password the user can never use; OIDC sign-in bypasses
    # ``authenticate``. Schema requires password_hash; storing a hash
    # of a random secret keeps the column non-null without inventing a
    # sentinel.
    return users_repo.create(email=email, password=secrets.token_hex(32), name=name)
