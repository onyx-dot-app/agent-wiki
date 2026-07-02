"""Trigger destinations repo.

A *destination* is the catalog row a destination config's ``type``
points at — i.e. where a fire is delivered. The seeded ``event_log``
destination means "record the fire to the events table; don't dispatch
outbound." Future destinations (webhook, agent message, …) get added by
migration as their dispatchers come online.

The repo owns destination-id validation. Repos / API / agent tools call
:func:`exists` instead of holding their own allow-list, so adding a new
destination is a one-line migration plus a dispatcher — no edits to the
trigger-creation surface.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.db.models import TriggerDestination
from app.db.session import session

# Stable slug of the always-present destination — fires recorded to the
# events table with no outbound dispatch. Imported elsewhere so we don't
# spread the literal across the codebase.
EVENT_LOG_ID = "event_log"

# Slug of the Slack destination (seeded by migration 0023). When a fire's
# destination is this, the dispatcher in ``app/tasks/triggers.py`` POSTs the
# rendered message to the admin-configured incoming webhook. Same
# "don't spread the literal" rationale as ``EVENT_LOG_ID``.
SLACK_ID = "slack"

# Slug of the email destination (seeded by migration 0048). Fires deliver to
# the config's verified address; unverified configs are recorded-only.
EMAIL_ID = "email"


def _to_dict(d: TriggerDestination) -> dict[str, Any]:
    return {
        "id": d.id,
        "name": d.name,
        "description": d.description,
        "created_at": d.created_at,
    }


def list_all() -> list[dict[str, Any]]:
    with session() as s:
        rows = s.scalars(
            select(TriggerDestination).order_by(TriggerDestination.id)
        ).all()
        return [_to_dict(d) for d in rows]


def get(destination_id: str) -> dict[str, Any] | None:
    with session() as s:
        row = s.get(TriggerDestination, destination_id)
        return _to_dict(row) if row else None


def exists(destination_id: str) -> bool:
    with session() as s:
        return s.get(TriggerDestination, destination_id) is not None
