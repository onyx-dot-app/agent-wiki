"""Per-user trigger destination-config registry.

A *destination config* is a user-owned, named, typed delivery target a trigger
can fire to — a Slack channel, an outbound webhook, an email address, … This
generalizes ``app/slack/webhooks.py`` (Slack-only) into a typed registry:
``type`` is a ``trigger_destinations`` catalog slug, ``config`` holds the
non-secret per-type settings, and ``secret`` is the optional encrypted
credential.

Configs are private to ``owner_user_id`` — list/get/delete are owner-scoped and
``get_secret`` enforces ownership before returning the decrypted secret. The
secret is never written to the wiki git repo. Repos return dicts, not ORM rows.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db.models import DestinationConfig
from app.db.session import session
from app.triggers import destinations

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_dict(d: DestinationConfig) -> dict[str, Any]:
    # Secret is deliberately omitted — callers that need it go through
    # ``get_secret``, which enforces ownership.
    return {
        "id": d.id,
        "owner_user_id": d.owner_user_id,
        "type": d.type,
        "name": d.name,
        "config": d.config_json,
        "has_secret": d.secret is not None,
        "verified_at": d.verified_at,
        "created_at": d.created_at,
    }


def list_for_user(user_id: str) -> list[dict[str, Any]]:
    """A user's destination configs, newest first. Secrets omitted."""
    with session() as s:
        rows = s.scalars(
            select(DestinationConfig)
            .where(DestinationConfig.owner_user_id == user_id)
            .order_by(DestinationConfig.created_at.desc())
        ).all()
        return [_to_dict(d) for d in rows]


def create(
    user_id: str,
    *,
    type: str,
    name: str,
    config: dict[str, Any] | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    """Register a destination config for a user. Validates the type slug
    against the ``trigger_destinations`` catalog."""
    name = name.strip()
    if not name:
        raise ValueError("name is required")
    if not destinations.exists(type):
        raise ValueError(f"unknown destination type: {type}")
    if type == destinations.SLACK_ID:
        cfg = config or {}
        targets = sum(
            1 for present in (secret, cfg.get("channel_id"), cfg.get("dm")) if present
        )
        if targets != 1:
            raise ValueError(
                "a slack destination needs exactly one of: a webhook secret, "
                "a channel_id, or dm: true"
            )
    if type == destinations.EMAIL_ID:
        address = ((config or {}).get("address") or "")
        if not isinstance(address, str) or "@" not in address.strip():
            raise ValueError("an email destination needs config.address")

    config_id = "dst_" + uuid.uuid4().hex[:12]
    created_at = _now_iso()
    with session() as s:
        s.add(
            DestinationConfig(
                id=config_id,
                owner_user_id=user_id,
                type=type,
                name=name,
                config_json=config or {},
                secret=secret,
                created_at=created_at,
            )
        )
    log.info("destination config created id=%s user_id=%s type=%s", config_id, user_id, type)
    return {
        "id": config_id,
        "owner_user_id": user_id,
        "type": type,
        "name": name,
        "config": config or {},
        "has_secret": secret is not None,
        "created_at": created_at,
    }


def get(config_id: str, user_id: str) -> dict[str, Any] | None:
    """Owner-scoped fetch (secret omitted). None if missing or not owned."""
    with session() as s:
        d = s.get(DestinationConfig, config_id)
        if d is None or d.owner_user_id != user_id:
            return None
        return _to_dict(d)


def delete(config_id: str, user_id: str) -> bool:
    """Delete a config. Returns False if it doesn't exist or isn't owned by
    ``user_id`` (so the API can 404 vs 204 cleanly)."""
    with session() as s:
        d = s.get(DestinationConfig, config_id)
        if d is None or d.owner_user_id != user_id:
            return False
        s.delete(d)
    log.info("destination config deleted id=%s user_id=%s", config_id, user_id)
    return True


def owned_by(config_id: str, user_id: str) -> bool:
    """True iff ``config_id`` exists and belongs to ``user_id``."""
    with session() as s:
        d = s.get(DestinationConfig, config_id)
        return d is not None and d.owner_user_id == user_id


def get_secret(config_id: str, *, owner_user_id: str) -> str | None:
    """Resolve a config id to its decrypted secret, enforcing ownership.

    Returns None if the config is missing, owned by someone else, or has no
    secret — the dispatcher treats that as "skip outbound" (the fire is still
    recorded)."""
    with session() as s:
        d = s.get(DestinationConfig, config_id)
        if d is None or d.owner_user_id != owner_user_id:
            return None
        return d.secret
