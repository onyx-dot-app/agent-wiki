"""FastAPI port of ``app/api/events.py`` (Phase 2)."""
from __future__ import annotations

import json
import logging
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, not_, or_, select

from app.auth import User
from app.auth import users as users_repo
from app.auth.deps import require_user
from app.db.models import Event as EventRow, Trigger, WikiOwner
from app.db.session import session
from app.models.event import Event, EventListResponse

router = APIRouter()
log = logging.getLogger(__name__)


def _parse_payload(raw: str) -> dict[str, Any]:
    try:
        parsed: Any = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        log.warning("malformed event payload_json: %r", raw[:200])
        return {}
    return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {}


def _to_view(e: EventRow, names: dict[str, str] | None = None) -> Event:
    return Event(
        id=e.id,
        ts=e.ts,
        kind=e.kind,
        actor=e.actor,
        actor_display=(names or {}).get(e.actor or ""),
        target=e.target,
        payload=_parse_payload(e.payload_json),
    )


def _display_names(actor_ids: set[str]) -> dict[str, str]:
    rows = users_repo.get_many(actor_ids)
    return {
        uid: str(row.get("name") or row.get("email") or "")
        for uid, row in rows.items()
        if row.get("name") or row.get("email")
    }


@router.get("", response_model=EventListResponse)
def list_events(
    user: User = Depends(require_user),
    limit: int = Query(100, ge=1, le=500),
    kind: str | None = None,
) -> EventListResponse:
    # The activity feed shows two families of events keyed on ``target``:
    # trigger fires (target = a trigger the user owns) and page-scoped events
    # like ``wiki.frequent_updates`` (target = a page the user owns).
    owned_trigger_ids = select(Trigger.id).where(Trigger.owner_user_id == user.id)
    owned_page_paths = select(WikiOwner.path).where(
        WikiOwner.owner_user_id == user.id
    )
    visibility = or_(
        EventRow.target.in_(owned_trigger_ids),
        EventRow.target.in_(owned_page_paths),
    )
    # Auto Organize auto-applies cleanups across the space, often on paths with
    # no explicit owner — an admin audit concern. So admins additionally see
    # every ``automanage.*`` event regardless of ownership.
    if user.is_admin:
        visibility = or_(visibility, EventRow.kind.like("automanage.%"))
    stmt = (
        select(EventRow)
        .where(visibility)
        # Your own comments are not news to you, they stay visible to
        # everyone else who can see the page's events.
        .where(
            not_(
                and_(EventRow.kind == "page.comment", EventRow.actor == user.id)
            )
        )
        .order_by(EventRow.id.desc())
        .limit(limit)
    )
    if kind:
        stmt = stmt.where(EventRow.kind == kind)

    with session() as s:
        rows = s.scalars(stmt).all()

    names = _display_names({e.actor for e in rows if e.actor})
    return EventListResponse(events=[_to_view(e, names) for e in rows])


@router.get("/{event_id}", response_model=Event)
def get_event(event_id: int, user: User = Depends(require_user)) -> Event:
    with session() as s:
        e = s.get(EventRow, event_id)
        if e is None:
            raise HTTPException(status_code=404, detail="not found")
        # Admins can read any automanage event (mirrors the list's audit view).
        if user.is_admin and e.kind.startswith("automanage."):
            return _to_view(e, _display_names({e.actor} if e.actor else set()))
        # Hide cross-owner rows behind a 404 so we don't leak existence.
        if e.target is None:
            raise HTTPException(status_code=404, detail="not found")
        owner = s.scalar(
            select(Trigger.owner_user_id).where(Trigger.id == e.target)
        )
        if owner != user.id:
            raise HTTPException(status_code=404, detail="not found")
        return _to_view(e, _display_names({e.actor} if e.actor else set()))
