"""Audit log of events. Time-filtered + paginated."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.auth import login_required

bp = Blueprint("events", __name__)


@bp.get("")
@login_required
def list_events():
    # query: ?kind=&since=&until=&cursor=&limit=
    # TODO: paginate over events table.
    raise NotImplementedError


@bp.get("/<int:event_id>")
@login_required
def get_event(event_id: int):
    raise NotImplementedError
