"""Repo for ``agent_sessions`` — one row per Run-Agent invocation.

See ``local_data/wiki/Wiki Project/Specific Features/coding_tool_launchers/design.md``.

Idle / close thresholds read from ``CONFIG.*``. Heartbeat /
activity-touch refuses to update closed/failed sessions.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import select, update

from app.config import CONFIG
from app.db.models import AgentSession
from app.db.session import execute_dml, session

log = logging.getLogger(__name__)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _now_iso() -> str:
    return _iso(datetime.now(timezone.utc))


def _to_dict(row: AgentSession) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "machine_id": row.machine_id,
        "tool_id": row.tool_id,
        "wiki_path": row.wiki_path,
        "working_dir": row.working_dir,
        "first_turn_prompt": row.first_turn_prompt,
        "cli_session_id": row.cli_session_id,
        "status": row.status,
        "started_at": row.started_at,
        "last_activity_at": row.last_activity_at,
        "spawn_ok_at": row.spawn_ok_at,
        "closed_at": row.closed_at,
    }


def _to_summary(row: AgentSession) -> dict[str, Any]:
    """Same shape as ``_to_dict`` minus ``first_turn_prompt`` —
    defense-in-depth keeps page bodies out of list responses."""
    d = _to_dict(row)
    d.pop("first_turn_prompt", None)
    return d


# --------------------------------------------------------------------------- #
# Create / read                                                               #
# --------------------------------------------------------------------------- #


def create(
    *,
    user_id: str,
    tool_id: str,
    first_turn_prompt: str,
    wiki_path: str | None,
    working_dir: str | None,
    machine_id: str | None = None,
    cli_session_id: str | None = None,
) -> str:
    sid = "as_" + uuid.uuid4().hex
    with session() as s:
        s.add(
            AgentSession(
                id=sid,
                user_id=user_id,
                machine_id=machine_id,
                tool_id=tool_id,
                wiki_path=wiki_path,
                working_dir=working_dir,
                first_turn_prompt=first_turn_prompt,
                cli_session_id=cli_session_id,
            )
        )
    log.info("agent_session created id=%s user=%s tool=%s", sid, user_id, tool_id)
    return sid


def get(sid: str) -> dict[str, Any] | None:
    with session() as s:
        row = s.get(AgentSession, sid)
        return _to_dict(row) if row is not None else None


def list_for_user(
    user_id: str,
    *,
    statuses: Iterable[str] = ("pending", "active", "idle"),
) -> list[dict[str, Any]]:
    statuses_t = tuple(statuses)
    with session() as s:
        rows = s.scalars(
            select(AgentSession)
            .where(AgentSession.user_id == user_id, AgentSession.status.in_(statuses_t))
            .order_by(AgentSession.started_at.desc())
        ).all()
        return [_to_summary(r) for r in rows]


def list_for_page(*, user_id: str, wiki_path: str) -> list[dict[str, Any]]:
    with session() as s:
        rows = s.scalars(
            select(AgentSession)
            .where(
                AgentSession.user_id == user_id,
                AgentSession.wiki_path == wiki_path,
                AgentSession.status.in_(("pending", "active", "idle")),
            )
            .order_by(AgentSession.started_at.desc())
        ).all()
        return [_to_summary(r) for r in rows]


# --------------------------------------------------------------------------- #
# Mutate                                                                      #
# --------------------------------------------------------------------------- #


def mark_active(sid: str, *, machine_id: str) -> None:
    now = _now_iso()
    with session() as s:
        updated = execute_dml(
            s,
            update(AgentSession)
            .where(
                AgentSession.id == sid,
                AgentSession.status == "pending",
            )
            .values(status="active", machine_id=machine_id, last_activity_at=now),
        )
    if updated == 0:
        log.info("agent_session mark_active ignored id=%s (status not pending)", sid)


def set_cli_session_id(sid: str, cli_session_id: str) -> None:
    with session() as s:
        s.execute(
            update(AgentSession)
            .where(AgentSession.id == sid)
            .values(cli_session_id=cli_session_id, last_activity_at=_now_iso())
        )


def mark_spawn_ok(sid: str) -> None:
    """Helper POSTs immediately after handing the spawn off to
    Terminal.app. Sweep watches for this — if it never arrives, the
    session is marked ``failed`` 30s post-exchange.
    """
    now = _now_iso()
    with session() as s:
        s.execute(
            update(AgentSession)
            .where(AgentSession.id == sid)
            .values(spawn_ok_at=now, last_activity_at=now)
        )


def touch_activity(sid: str) -> None:
    """Bump ``last_activity_at`` — refuses to resurrect closed/failed
    sessions."""
    with session() as s:
        s.execute(
            update(AgentSession)
            .where(
                AgentSession.id == sid,
                AgentSession.status.in_(("pending", "active", "idle")),
            )
            .values(last_activity_at=_now_iso())
        )


def close(sid: str, *, reason: str) -> None:
    now = _now_iso()
    with session() as s:
        s.execute(
            update(AgentSession)
            .where(AgentSession.id == sid)
            .values(status="closed", closed_at=now, last_activity_at=now)
        )
    log.info("agent_session closed id=%s reason=%s", sid, reason)


def mark_failed(sid: str, *, reason: str) -> None:
    now = _now_iso()
    with session() as s:
        s.execute(
            update(AgentSession)
            .where(AgentSession.id == sid)
            .values(status="failed", closed_at=now, last_activity_at=now)
        )
    log.info("agent_session failed id=%s reason=%s", sid, reason)


# --------------------------------------------------------------------------- #
# Sweep                                                                       #
# --------------------------------------------------------------------------- #


def mark_stale_idle() -> int:
    cutoff = _iso(datetime.now(timezone.utc) - timedelta(seconds=CONFIG.agent_session_idle_seconds))
    with session() as s:
        return execute_dml(
            s,
            update(AgentSession)
            .where(
                AgentSession.status == "active",
                AgentSession.last_activity_at <= cutoff,
            )
            .values(status="idle"),
        )


def evict_idle_to_closed() -> int:
    cutoff = _iso(
        datetime.now(timezone.utc)
        - timedelta(seconds=CONFIG.agent_session_close_after_idle_seconds)
    )
    now = _now_iso()
    with session() as s:
        return execute_dml(
            s,
            update(AgentSession)
            .where(
                AgentSession.status == "idle",
                AgentSession.last_activity_at <= cutoff,
            )
            .values(status="closed", closed_at=now),
        )


def evict_spawn_missed() -> int:
    """Sessions that exchanged but never reported spawn_ok within 30s
    are marked ``failed`` so the UI stops showing them as live."""
    cutoff = _iso(datetime.now(timezone.utc) - timedelta(seconds=30))
    now = _now_iso()
    with session() as s:
        return execute_dml(
            s,
            update(AgentSession)
            .where(
                AgentSession.status == "active",
                AgentSession.spawn_ok_at.is_(None),
                AgentSession.last_activity_at <= cutoff,
            )
            .values(status="failed", closed_at=now),
        )
