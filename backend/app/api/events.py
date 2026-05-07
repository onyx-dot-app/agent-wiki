"""Audit log of events.

V0 scope: list newest-first with a simple limit. Time filters / pagination
come later; the table has indices on ``ts`` and ``(kind, ts)`` so this is
cheap to extend when needed.
"""
from __future__ import annotations

import json

from flask import Blueprint, jsonify, request

from app.auth import login_required
from app.db.sqlite import connect

bp = Blueprint("events", __name__)


def _parse_payload(raw: str) -> dict:
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


@bp.get("")
@login_required
def list_events():
    try:
        limit = int(request.args.get("limit", 100))
    except ValueError:
        return jsonify(error="limit must be an integer"), 400
    limit = max(1, min(limit, 500))
    kind = request.args.get("kind")

    conn = connect()
    try:
        if kind:
            rows = conn.execute(
                "SELECT id, ts, kind, actor, target, payload_json FROM events "
                "WHERE kind = ? ORDER BY id DESC LIMIT ?",
                (kind, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, ts, kind, actor, target, payload_json FROM events "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    finally:
        conn.close()

    return jsonify(
        events=[
            {
                "id": r["id"],
                "ts": r["ts"],
                "kind": r["kind"],
                "actor": r["actor"],
                "target": r["target"],
                "payload": _parse_payload(r["payload_json"]),
            }
            for r in rows
        ]
    )


@bp.get("/<int:event_id>")
@login_required
def get_event(event_id: int):
    conn = connect()
    try:
        row = conn.execute(
            "SELECT id, ts, kind, actor, target, payload_json FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return jsonify(error="not found"), 404
    return jsonify(
        id=row["id"],
        ts=row["ts"],
        kind=row["kind"],
        actor=row["actor"],
        target=row["target"],
        payload=_parse_payload(row["payload_json"]),
    )
