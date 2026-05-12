"""Agent activity registry — per-agent visibility for active work.

The DB (`agent_activity` table) is the source of truth. Each row says
which user (and optionally which named agent) is currently reading or
writing a wiki doc, with a TTL. There is **one row per
(user, agent)** at any moment — a newer upsert replaces the prior row
in place. The state is exposed through:

* The ``read_page`` / ``read_doc`` tool responses (an ``agents`` field
  on every HEAD read), so co-occupant agents can see each other.
* ``GET /api/wiki/file/activity?path=...`` for the UI panel.

The doc body itself is *not* touched — there is no on-disk
representation. This avoids the read→commit churn that broke
``base_sha`` optimistic concurrency when activity was rendered as
frontmatter on each ``.md``.

Lifecycle:
* ``read`` registers on successful HEAD reads via ``read_page`` /
  ``read_doc``, using ``DEFAULT_TTL`` (24h).
* ``wrote`` registers on successful writes through the doc-edit
  tools. Write tools accept an optional ``expires_in_seconds`` arg
  letting an agent declare "I'll be working on this for X seconds";
  that value becomes the row's TTL for that upsert.
* Each upsert overwrites ``expires_at``. ``read`` after a custom
  ``expires_in_seconds`` resets to the 24h default — the most
  recent action wins.
* At ``expires_at`` the cleanup task in ``app/tasks/agent_activity.py``
  deletes the row. Server restart re-schedules cleanups for every
  active row.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel
from sqlalchemy import func, select

from app.db.models import AgentActivity, User
from app.db.session import session

log = logging.getLogger(__name__)


DEFAULT_TTL = timedelta(hours=24)


# Optional per-request agent identity. Set by an agent entrypoint when a
# name is meaningful. Default ``None`` renders as ``N/A`` to API
# consumers and the natural-key index treats it as "anonymous for this
# user".
agent_name_var: ContextVar[str | None] = ContextVar("agent_name", default=None)


# --------------------------------------------------------------------------- #
# Time helpers                                                                #
# --------------------------------------------------------------------------- #


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(ts: datetime) -> str:
    return ts.isoformat()


# --------------------------------------------------------------------------- #
# DB layer                                                                    #
# --------------------------------------------------------------------------- #


class ActivityRow(BaseModel):
    """A row from `agent_activity` joined with the user's display name."""

    id: int
    user_id: str
    owner_display: str
    agent_name: str | None
    doc_path: str
    activity: str
    description: str | None
    registered_at: str
    expires_at: str


def _row_to_dict(activity: AgentActivity, owner_display: str) -> ActivityRow:
    return ActivityRow(
        id=activity.id,
        user_id=activity.user_id,
        owner_display=owner_display,
        agent_name=activity.agent_name,
        doc_path=activity.doc_path,
        activity=activity.activity,
        description=activity.description,
        registered_at=activity.registered_at,
        expires_at=activity.expires_at,
    )


def _select_with_owner():
    """Build the (AgentActivity, owner_display) select used by every list/get."""
    owner_display = func.coalesce(User.name, User.email).label("owner_display")
    return select(AgentActivity, owner_display).join(User, User.id == AgentActivity.user_id)


def list_for_doc(doc_path: str) -> list[ActivityRow]:
    """All non-expired rows for a doc, sorted for stable rendering."""
    now = _iso(_now())
    with session() as s:
        rows = s.execute(
            _select_with_owner()
            .where(AgentActivity.doc_path == doc_path, AgentActivity.expires_at > now)
            .order_by(
                "owner_display",
                func.coalesce(AgentActivity.agent_name, ""),
                AgentActivity.activity,
            )
        ).all()
        return [_row_to_dict(activity, owner_display) for activity, owner_display in rows]


def list_all_active() -> list[ActivityRow]:
    """Every non-expired row across all docs. Used by the restart scan."""
    now = _iso(_now())
    with session() as s:
        rows = s.execute(
            _select_with_owner()
            .where(AgentActivity.expires_at > now)
            .order_by(AgentActivity.expires_at.asc())
        ).all()
        return [_row_to_dict(activity, owner_display) for activity, owner_display in rows]


def list_all_expired() -> list[ActivityRow]:
    """Every row whose expiry is already in the past."""
    now = _iso(_now())
    with session() as s:
        rows = s.execute(_select_with_owner().where(AgentActivity.expires_at <= now)).all()
        return [_row_to_dict(activity, owner_display) for activity, owner_display in rows]


def upsert_activity(
    *,
    user_id: str,
    agent_name: str | None,
    doc_path: str,
    activity: str,
    description: str | None,
    ttl: timedelta = DEFAULT_TTL,
    agent_session_id: str | None = None,
) -> str:
    """UPSERT a row. Returns the resulting `expires_at` ISO string.

    The natural key is ``(user_id, agent_name)`` — there is exactly one
    row per (user, agent) at any moment. A new upsert overwrites the
    prior row's ``doc_path``, ``activity``, ``description``,
    ``registered_at``, ``expires_at``, and clears ``cleanup_msg_id``
    (the prior scheduled fire is canceled by
    ``schedule_cleanup_for_natural_key`` after this returns).
    """
    if activity not in ("read", "wrote"):
        raise ValueError(f"unsupported activity: {activity!r}")
    now = _now()
    expires_at = _iso(now + ttl)
    registered_at = _iso(now)
    with session() as s:
        existing = s.scalar(
            select(AgentActivity).where(
                AgentActivity.user_id == user_id,
                AgentActivity.agent_name.is_not_distinct_from(agent_name),
            )
        )
        if existing is not None:
            existing.doc_path = doc_path
            existing.activity = activity
            existing.description = description
            existing.registered_at = registered_at
            existing.expires_at = expires_at
            existing.agent_session_id = agent_session_id
        else:
            s.add(
                AgentActivity(
                    user_id=user_id,
                    agent_name=agent_name,
                    doc_path=doc_path,
                    activity=activity,
                    description=description,
                    registered_at=registered_at,
                    expires_at=expires_at,
                    agent_session_id=agent_session_id,
                )
            )
    log.debug(
        "agent_activity upsert user=%s agent=%s doc=%s activity=%s expires=%s",
        user_id,
        agent_name,
        doc_path,
        activity,
        expires_at,
    )
    return expires_at


def get_by_natural_key(*, user_id: str, agent_name: str | None) -> ActivityRow | None:
    with session() as s:
        row = s.execute(
            _select_with_owner().where(
                AgentActivity.user_id == user_id,
                AgentActivity.agent_name.is_not_distinct_from(agent_name),
            )
        ).first()
        if row is None:
            return None
        a, owner_display = row
        return _row_to_dict(a, owner_display)


def delete_by_natural_key(*, user_id: str, agent_name: str | None) -> None:
    with session() as s:
        existing = s.scalar(
            select(AgentActivity).where(
                AgentActivity.user_id == user_id,
                AgentActivity.agent_name.is_not_distinct_from(agent_name),
            )
        )
        if existing is not None:
            s.delete(existing)


def delete_for_doc(doc_path: str) -> None:
    with session() as s:
        rows = s.scalars(select(AgentActivity).where(AgentActivity.doc_path == doc_path)).all()
        for r in rows:
            s.delete(r)


def rename_doc(old_path: str, new_path: str) -> None:
    with session() as s:
        rows = s.scalars(select(AgentActivity).where(AgentActivity.doc_path == old_path)).all()
        for r in rows:
            r.doc_path = new_path
