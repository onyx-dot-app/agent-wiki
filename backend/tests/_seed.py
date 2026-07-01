"""Test-side seed/inspection helpers built on the ORM session.

Pulled out of individual test files so the boilerplate for inserting a
user, a trigger, or an event isn't repeated in every module. Imported as
``from tests._seed import ...``.

These deliberately mirror the small set of operations tests actually
need — there's no general-purpose query layer here, and there shouldn't
be. If a single test wants to assert on a one-off shape, do it inline.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete, func, select

from app.db.models import (
    AgentActivity,
    Event,
    Trigger,
    User,
)
from app.db.session import session


# --------------------------------------------------------------------------- #
# Users + triggers                                                            #
# --------------------------------------------------------------------------- #


def seed_user(
    uid: str = "usr_1",
    email: str = "u@x.com",
    *,
    password_hash: str = "x",
    is_admin: bool = False,
    name: str | None = None,
) -> str:
    with session() as s:
        s.add(
            User(
                id=uid,
                email=email,
                name=name,
                password_hash=password_hash,
                is_admin=is_admin,
            )
        )
    return uid


def seed_trigger(
    *,
    tid: str,
    owner_user_id: str,
    scope_path: str,
    nl_description: str = "fire when status changes",
    message: str | None = None,
    destination: str = "event_log",
    enabled: bool = True,
    kind: str = "delta",
    schedule_cron: str | None = None,
    schedule_timezone: str | None = None,
    schedule_start_at: str | None = None,
    schedule_last_fired_at: str | None = None,
    slack_webhook_id: str | None = None,
) -> str:
    action_json = json.dumps(
        {
            "actions": [
                {"type": destination, "message": message, "slack_webhook_id": slack_webhook_id}
            ]
        }
    )
    with session() as s:
        s.add(
            Trigger(
                id=tid,
                owner_user_id=owner_user_id,
                scope_path=scope_path,
                kind=kind,
                nl_description=nl_description,
                action_json=action_json,
                enabled=enabled,
                schedule_cron=schedule_cron,
                schedule_timezone=schedule_timezone,
                schedule_start_at=schedule_start_at,
                schedule_last_fired_at=schedule_last_fired_at,
            )
        )
    return tid


# --------------------------------------------------------------------------- #
# Events                                                                      #
# --------------------------------------------------------------------------- #


def insert_event(kind: str, target: str, payload: dict, *, actor: str | None = None) -> int:
    """Insert an ``events`` row directly. Returns the new ``id``."""
    with session() as s:
        e = Event(
            kind=kind,
            actor=actor,
            target=target,
            payload_json=json.dumps(payload),
        )
        s.add(e)
        s.flush()
        return int(e.id)


def list_events(kind: str | None = None) -> list[dict[str, Any]]:
    """Return events as plain dicts, newest first.

    Includes the parsed ``payload`` plus the raw ``payload_json`` so
    tests that need either field don't need a second helper.
    """
    stmt = select(Event).order_by(Event.id.desc())
    if kind is not None:
        stmt = stmt.where(Event.kind == kind)
    with session() as s:
        rows = s.scalars(stmt).all()
    return [
        {
            "id": e.id,
            "ts": e.ts,
            "kind": e.kind,
            "actor": e.actor,
            "target": e.target,
            "payload_json": e.payload_json,
            "payload": json.loads(e.payload_json) if e.payload_json else {},
        }
        for e in rows
    ]


def clear_events() -> None:
    with session() as s:
        s.execute(delete(Event))


# --------------------------------------------------------------------------- #
# BM25 search index inspection                                                #
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Misc                                                                        #
# --------------------------------------------------------------------------- #


def count_rows(model: Any) -> int:
    with session() as s:
        return s.scalar(select(func.count()).select_from(model)) or 0


__all__ = [
    "AgentActivity",
    "Event",
    "Trigger",
    "User",
    "clear_events",
    "count_rows",
    "insert_event",
    "list_events",
    "seed_trigger",
    "seed_user",
]
