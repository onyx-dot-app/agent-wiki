from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Event(BaseModel):
    id: int
    ts: str
    kind: str          # doc.update | trigger.fire | webhook.in | ...
    actor: str | None
    # Resolved display name for ``actor`` (feed rendering), None for system
    # actors or deleted users.
    actor_display: str | None = None
    target: str | None
    payload: dict[str, Any]


class EventListResponse(BaseModel):
    events: list[Event]
