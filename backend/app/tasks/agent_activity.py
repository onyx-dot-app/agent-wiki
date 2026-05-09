"""Cleanup tasks for the agent-activity registry.

Each row in the ``agent_activity`` table carries an ``expires_at``.
When that moment passes, the row should be deleted. We don't want to
poll — instead, every time a row is upserted we schedule a delayed
task at exactly ``expires_at``, and on server restart we re-schedule
the same for every active row.

Why ``triggers_queue``: cleanup is a small, time-driven side effect —
same shape as a scheduled trigger fire. Activity is DB-only (the doc
body is never touched), so the work is just a single ``DELETE``.

A cleanup is "stale" when the row has already been re-registered with
a later ``expires_at``: the new registration scheduled its own
cleanup, so the old fire is a no-op. We detect this by stamping the
scheduled task with the ``expires_at`` it was supposed to enforce and
comparing on fire.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import AgentActivity
from app.db.session import session
from app.tasks.queues import triggers_queue
from app.wiki import agent_activity

log = logging.getLogger(__name__)


@triggers_queue.task()
def cleanup_expired_activity(
    user_id: str,
    agent_name: str | None,
    doc_path: str,
    activity: str,
    expected_expires_at: str,
) -> None:
    row = agent_activity.get_by_natural_key(
        user_id=user_id,
        agent_name=agent_name,
        doc_path=doc_path,
        activity=activity,
    )
    if row is None:
        log.debug(
            "agent_activity cleanup: row already gone user=%s agent=%s doc=%s activity=%s",
            user_id, agent_name, doc_path, activity,
        )
        return
    if row.expires_at != expected_expires_at:
        # Re-registered with a new expiry; its own scheduled cleanup is
        # what should fire. This one is stale.
        log.debug(
            "agent_activity cleanup: stale fire (renewed); expected=%s current=%s",
            expected_expires_at, row.expires_at,
        )
        return
    agent_activity.delete_by_natural_key(
        user_id=user_id,
        agent_name=agent_name,
        doc_path=doc_path,
        activity=activity,
    )


def schedule_cleanup_for_natural_key(
    *,
    user_id: str,
    agent_name: str | None,
    doc_path: str,
    activity: str,
    expires_at: str,
) -> None:
    """Schedule a cleanup task to fire at ``expires_at``."""
    eta = _parse_eta(expires_at)
    cleanup_expired_activity.schedule(
        args=(user_id, agent_name, doc_path, activity, expires_at),
        eta=eta,
    )


def schedule_all_pending_cleanups() -> None:
    """Schedule a cleanup for every row in the registry.

    Past-due rows fire immediately. Future rows fire at their ``expires_at``.
    Called once at server startup so a restart never leaves rows orphaned.
    """
    now = datetime.now(timezone.utc)
    with session() as s:
        rows = s.scalars(select(AgentActivity)).all()
    if not rows:
        log.debug("agent_activity startup scan: no rows to schedule")
        return
    n_immediate = 0
    n_future = 0
    for r in rows:
        eta = _parse_eta(r.expires_at)
        if eta < now:
            eta = now
            n_immediate += 1
        else:
            n_future += 1
        cleanup_expired_activity.schedule(
            args=(r.user_id, r.agent_name, r.doc_path, r.activity, r.expires_at),
            eta=eta,
        )
    log.info(
        "agent_activity startup scan: scheduled %d immediate + %d future cleanups",
        n_immediate, n_future,
    )


def _parse_eta(expires_at: str) -> datetime:
    """Parse the stored ISO timestamp into a UTC-aware datetime.

    The queue's enqueue path converts an ``eta`` (timezone-aware datetime)
    into a pgmq delay in seconds; passing aware UTC keeps the math right
    regardless of where the worker process happens to run.
    """
    return datetime.fromisoformat(expires_at)
