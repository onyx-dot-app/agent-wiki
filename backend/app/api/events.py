"""Audit log of events.

V0 scope: list newest-first with a simple limit. Time filters / pagination
come later; the table has indices on ``ts`` and ``(kind, ts)`` so this is
cheap to extend when needed.

Owner-scoping (2026-05-09): ``trigger.fire`` rows reference the trigger's
id in ``target``, and triggers carry an ``owner_user_id``. Both endpoints
filter to events the caller owns by joining through that subquery —
non-fire events without a trigger id are excluded by design until we add
an explicit user attribution column for them. ``get_event`` returns
``404`` rather than ``403`` on cross-owner reads so we don't leak
existence.
"""
from __future__ import annotations

import json
import logging
from typing import Any, cast

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from app.auth import current_user, login_required
from app.db.models import Event as EventRow, Trigger
from app.db.session import session
from app.models.event import Event, EventListResponse
from app.models._helpers import error

bp = Blueprint("events", __name__)
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


@bp.get("")
@login_required
def list_events():
    user = current_user()
    assert user is not None
    try:
        limit = int(request.args.get("limit", 100))
    except ValueError:
        return error("limit must be an integer", 400)
    limit = max(1, min(limit, 500))
    kind = request.args.get("kind")

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

    return jsonify(EventListResponse(events=[_to_view(e) for e in rows]).model_dump())


@bp.get("/<int:event_id>")
@login_required
def get_event(event_id: int):
    user = current_user()
    assert user is not None
    with session() as s:
        e = s.get(EventRow, event_id)
        if e is None:
            return error("not found", 404)
        # Hide cross-owner rows behind a 404 so we don't leak existence.
        if e.target is None:
            return error("not found", 404)
        owner = s.scalar(
            select(Trigger.owner_user_id).where(Trigger.id == e.target)
        )
        if owner != user.id:
            return error("not found", 404)
        return jsonify(_to_view(e).model_dump())
