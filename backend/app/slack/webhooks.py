"""Per-user Slack webhook registry.

A *Slack webhook* is a user-owned, named incoming-webhook URL (one per
channel). Triggers reference one via ``Trigger.slack_webhook_id``; the
dispatcher in ``app/tasks/triggers.py`` resolves the id to a URL and POSTs.

Webhooks are private to ``owner_user_id`` — list/get/delete are all
owner-scoped, and ``get_url`` enforces ownership before returning the
secret. The ``webhook_url`` is a secret: it's returned in full only to its
owner (so they can manage it) and is never written to the wiki git repo.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db.models import SlackWebhook
from app.db.session import session

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_dict(w: SlackWebhook) -> dict[str, Any]:
    return {
        "id": w.id,
        "owner_user_id": w.owner_user_id,
        "name": w.name,
        "webhook_url": w.webhook_url,
        "created_at": w.created_at,
    }


def list_for_user(user_id: str) -> list[dict[str, Any]]:
    """A user's webhooks, newest first."""
    with session() as s:
        rows = s.scalars(
            select(SlackWebhook)
            .where(SlackWebhook.owner_user_id == user_id)
            .order_by(SlackWebhook.created_at.desc())
        ).all()
        return [_to_dict(w) for w in rows]


def create(user_id: str, name: str, webhook_url: str) -> dict[str, Any]:
    """Register a named webhook for a user. Trims + validates inputs."""
    name = name.strip()
    if not name:
        raise ValueError("name is required")
    webhook_url = webhook_url.strip()
    if not webhook_url.startswith("https://hooks.slack.com/"):
        raise ValueError("webhook_url must be a https://hooks.slack.com/… URL")

    webhook_id = "swh_" + uuid.uuid4().hex[:12]
    with session() as s:
        s.add(
            SlackWebhook(
                id=webhook_id,
                owner_user_id=user_id,
                name=name,
                webhook_url=webhook_url,
                created_at=_now_iso(),
            )
        )
    log.info("slack webhook created id=%s user_id=%s name=%s", webhook_id, user_id, name)
    return {
        "id": webhook_id,
        "owner_user_id": user_id,
        "name": name,
        "webhook_url": webhook_url,
        "created_at": _now_iso(),
    }


def delete(webhook_id: str, user_id: str) -> bool:
    """Delete a webhook. Returns False if it doesn't exist or isn't owned by
    ``user_id`` (so the API can 404 vs 204 cleanly)."""
    with session() as s:
        w = s.get(SlackWebhook, webhook_id)
        if w is None or w.owner_user_id != user_id:
            return False
        s.delete(w)
    log.info("slack webhook deleted id=%s user_id=%s", webhook_id, user_id)
    return True


def owned_by(webhook_id: str, user_id: str) -> bool:
    """True iff ``webhook_id`` exists and belongs to ``user_id``."""
    with session() as s:
        w = s.get(SlackWebhook, webhook_id)
        return w is not None and w.owner_user_id == user_id


def get_url(webhook_id: str, *, owner_user_id: str) -> str | None:
    """Resolve a webhook id to its URL, enforcing ownership.

    Returns None if the webhook is missing or owned by someone else — the
    dispatcher treats that as "skip outbound" (the fire is still recorded).
    """
    with session() as s:
        w = s.get(SlackWebhook, webhook_id)
        if w is None or w.owner_user_id != owner_user_id:
            return None
        return w.webhook_url
