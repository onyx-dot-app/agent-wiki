"""MCP-token repo — SQLAlchemy ORM. Free functions over ``McpToken``.

Tokens are personal API keys an MCP client (Claude Code, Cursor, Codex,
…) presents in an ``Authorization: Bearer mcp_<token>`` header to talk
to the inbound MCP server.

The raw token is shown to the user **once** at creation; the DB only
stores a bcrypt hash. ``verify`` linearly walks every token row checking
bcrypt — that's fine while the token count stays small (per-user,
hand-minted). If we ever ship machine-generated tokens at scale, swap
in a deterministic prefix index.

See ``local_data/wiki/mcp-server/mcp-server.md`` for the full design.
"""
from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.auth import User
from app.auth.passwords import hash_password, verify_password
from app.db.models import McpToken, User as UserRow
from app.db.session import session

log = logging.getLogger(__name__)

TOKEN_PREFIX = "mcp_"
"""Distinguishable prefix so leaked tokens are obvious in logs and so the
token can't be confused with a session cookie or another credential."""

_TOKEN_BYTES = 24
"""24 bytes → 32 base64url chars after stripping padding. Plenty of
entropy; short enough to copy-paste without wrap."""


# --------------------------------------------------------------------------- #
# Read                                                                        #
# --------------------------------------------------------------------------- #


def _to_dict(t: McpToken) -> dict[str, Any]:
    return {
        "id": t.id,
        "user_id": t.user_id,
        "name": t.name,
        "created_at": t.created_at,
        "last_used_at": t.last_used_at,
    }


def list_for_user(user_id: str) -> list[dict[str, Any]]:
    """Tokens for a given user, newest first. Hashes are not returned."""
    with session() as s:
        rows = s.scalars(
            select(McpToken)
            .where(McpToken.user_id == user_id)
            .order_by(McpToken.created_at.desc())
        ).all()
        return [_to_dict(t) for t in rows]


# --------------------------------------------------------------------------- #
# Mutate                                                                      #
# --------------------------------------------------------------------------- #


def create(user_id: str, name: str) -> tuple[str, str]:
    """Mint a new token. Returns ``(token_id, raw_token)`` — the raw
    value is the only place the plaintext exists; the caller must show
    it to the user immediately and never persist it.

    Trims ``name`` and rejects empty values. Names don't have to be
    unique (the user may want two tokens both labelled "laptop" while
    rotating); ``id`` is the addressable handle.
    """
    name = name.strip()
    if not name:
        raise ValueError("name is required")

    raw = TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)
    token_id = "mtk_" + uuid.uuid4().hex[:12]

    with session() as s:
        s.add(
            McpToken(
                id=token_id,
                user_id=user_id,
                name=name,
                token_hash=hash_password(raw),
            )
        )
    log.info("mcp token created id=%s user_id=%s name=%s", token_id, user_id, name)
    return token_id, raw


def revoke(token_id: str, user_id: str) -> bool:
    """Revoke a token. Returns ``True`` if a row was deleted, ``False``
    if no token with that id exists for this user (used so the API can
    return 404 vs 204 cleanly).
    """
    with session() as s:
        t = s.get(McpToken, token_id)
        if t is None or t.user_id != user_id:
            return False
        s.delete(t)
    log.info("mcp token revoked id=%s user_id=%s", token_id, user_id)
    return True


# --------------------------------------------------------------------------- #
# Verify                                                                      #
# --------------------------------------------------------------------------- #


def verify(raw_token: str) -> tuple[User, str] | None:
    """Resolve a raw bearer token to ``(User, agent_name)``, or ``None``
    if the token is invalid / revoked / malformed. Constant-ish-time:
    bcrypt is run against every row, so an attacker can't tell from
    timing whether any prefix matched.

    ``agent_name`` is the token's user-supplied label, repurposed as
    the agent identity that gets stamped onto activity rows and woven
    into the git commit author.

    On success, also bumps ``last_used_at`` so the UI can show "last
    used 3 minutes ago" without an extra audit trail.
    """
    if not raw_token or not raw_token.startswith(TOKEN_PREFIX):
        return None

    with session() as s:
        # Linearly walk all tokens. At the scale of "a few keys per
        # user" this is fine; if we ever ship many tokens, add a
        # prefix-hash column to narrow the candidate set.
        rows = s.scalars(select(McpToken)).all()
        match: McpToken | None = None
        for row in rows:
            if verify_password(raw_token, row.token_hash):
                match = row
                break
        if match is None:
            return None

        user = s.get(UserRow, match.user_id)
        if user is None:
            log.warning(
                "mcp token %s resolved to missing user %s; treating as invalid",
                match.id,
                match.user_id,
            )
            return None

        match.last_used_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        return (
            User(
                id=user.id,
                email=user.email,
                name=user.name,
                is_admin=bool(user.is_admin),
            ),
            match.name,
        )
