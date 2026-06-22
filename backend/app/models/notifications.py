"""HTTP shapes for the notification center (/api/notifications)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class NotificationView(BaseModel):
    id: int
    notif_type: str
    title: str
    description: str | None
    dismissed: bool
    first_shown: str
    last_shown: str
    data: dict[str, Any]


class NotificationList(BaseModel):
    notifications: list[NotificationView]
    total_items: int
    undismissed_count: int
    has_more: bool


class DismissAllResponse(BaseModel):
    dismissed: int
