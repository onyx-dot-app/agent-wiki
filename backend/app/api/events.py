"""FastAPI port of ``app/api/events.py`` (Phase 2)."""
from __future__ import annotations

import json
import logging
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.auth import User
from app.auth.deps import require_user
from app.db.models import Event as EventRow, Trigger
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


def _to_view(e: EventRow) -> Event:
    return Event(
        id=e.id,
        ts=e.ts,
        kind=e.kind,
        actor=e.actor,
        target=e.target,
        payload=_parse_payload(e.payload_json),
    )


@router.get("", response_model=EventListResponse)
def list_events(
    user: User = Depends(require_user),
    limit: int = Query(100, ge=1, le=500),
    kind: str | None = None,
) -> EventListResponse:
    owned_trigger_ids = select(Trigger.id).where(Trigger.owner_user_id == user.id)
    stmt = (
        select(EventRow)
        .where(EventRow.target.in_(owned_trigger_ids))
        .order_by(EventRow.id.desc())
        .limit(limit)
    )
    if kind:
        stmt = stmt.where(EventRow.kind == kind)

    with session() as s:
        rows = s.scalars(stmt).all()

    return EventListResponse(events=[_to_view(e) for e in rows])


@router.get("/{event_id}", response_model=Event)
def get_event(event_id: int, user: User = Depends(require_user)) -> Event:
    with session() as s:
        e = s.get(EventRow, event_id)
        if e is None:
            raise HTTPException(status_code=404, detail="not found")
        # Hide cross-owner rows behind a 404 so we don't leak existence.
        if e.target is None:
            raise HTTPException(status_code=404, detail="not found")
        owner = s.scalar(
            select(Trigger.owner_user_id).where(Trigger.id == e.target)
        )
        if owner != user.id:
            raise HTTPException(status_code=404, detail="not found")
        return _to_view(e)
