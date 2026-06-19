"""HTTP API for the per-user notification center (header bell / inbox).

Routes mounted under ``/api/notifications`` from ``app.main:create_app``:

- ``GET  /api/notifications``               — newest-first page + badge counts
- ``POST /api/notifications/{id}/dismiss``  — mark one read
- ``POST /api/notifications/dismiss-all``   — mark everything read
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import User
from app.auth.deps import require_user
from app.db import notifications as notifications_repo
from app.models.notifications import DismissAllResponse, NotificationList, NotificationView

router = APIRouter()


@router.get("", response_model=NotificationList)
def list_notifications(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(require_user),
) -> NotificationList:
    page = notifications_repo.list_for_user(user.id, limit=limit, offset=offset)
    return NotificationList(
        notifications=[NotificationView(**n) for n in page["notifications"]],
        total_items=page["total_items"],
        undismissed_count=page["undismissed_count"],
        has_more=page["has_more"],
    )


@router.post("/{notification_id}/dismiss")
def dismiss(notification_id: int, user: User = Depends(require_user)) -> dict[str, bool]:
    if not notifications_repo.dismiss(notification_id, user_id=user.id):
        raise HTTPException(status_code=404, detail="notification not found")
    return {"ok": True}


@router.post("/dismiss-all", response_model=DismissAllResponse)
def dismiss_all(user: User = Depends(require_user)) -> DismissAllResponse:
    return DismissAllResponse(dismissed=notifications_repo.dismiss_all(user.id))
