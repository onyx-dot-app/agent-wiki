"""Cleanup tasks for the agent-activity registry.

Each row in the ``agent_activity`` table carries an ``expires_at``.
When that moment passes, the row should be deleted. We don't want to
poll — instead, every time a row is upserted we schedule a delayed
task at exactly ``expires_at``, and on server restart we re-schedule
the same for every active row.

Re-registration cancels the prior scheduled cleanup: each row stores
the queue ``msg_id`` of its current scheduled fire in
``cleanup_msg_id``. When ``upsert_activity`` slides ``expires_at``
forward, ``schedule_cleanup_for_natural_key`` enqueues a fresh task,
cancels the old msg_id, and writes the new id back. Without this,
every re-read would leave the prior delayed message orphaned for the
full TTL.

A cleanup is "stale" when the row has already been re-registered with
a later ``expires_at`` (the cancel may have raced the read). The
``expected_expires_at`` check below skips that fire as a no-op.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import AgentActivity
from app.db.session import session
from app.tasks.queue import cancel_delayed_message
from app.tasks.queues import lightweight_maintenance_queue
from app.wiki import agent_activity

log = logging.getLogger(__name__)


@lightweight_maintenance_queue.task()
def cleanup_expired_activity(
    user_id: str,
    agent_name: str | None,
    expected_expires_at: str,
) -> None:
    row = agent_activity.get_by_natural_key(
        user_id=user_id, agent_name=agent_name,
    )
    if row is None:
        log.debug(
            "agent_activity cleanup: row already gone user=%s agent=%s",
            user_id, agent_name,
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
        user_id=user_id, agent_name=agent_name,
    )


def schedule_cleanup_for_natural_key(
    *,
    user_id: str,
    agent_name: str | None,
    expires_at: str,
) -> None:
    """Schedule a cleanup at ``expires_at``, replacing any prior fire.

    Enqueues a fresh delayed message (cap-checked, gets back the new
    ``msg_id``), then reads the row's current ``cleanup_msg_id``,
    cancels the old delayed message, and writes the new id back.

    The two steps aren't atomic — if the process dies between them, we
    leak the new msg_id (it'll fire and no-op via the
    ``expected_expires_at`` stale check). Cheap.

    No-op if the row is gone (``upsert`` rolled back or it was deleted
    out from under us). The fresh msg is canceled in that case so we
    don't leak it.
    """
    eta = _parse_eta(expires_at)
    new_msg_id = cleanup_expired_activity.schedule(
        args=(user_id, agent_name, expires_at),
        eta=eta,
    )
    if new_msg_id is None:
        # Immediate mode (tests) — handler ran synchronously, nothing to
        # track or cancel.
        return
    with session() as s:
        row = s.scalar(
            select(AgentActivity).where(
                AgentActivity.user_id == user_id,
                AgentActivity.agent_name.is_not_distinct_from(agent_name),
            )
        )
        if row is None:
            log.warning(
                "agent_activity cleanup schedule: row gone, canceling new fire "
                "user=%s agent=%s",
                user_id, agent_name,
            )
            cancel_delayed_message(lightweight_maintenance_queue.name, new_msg_id)
            return
        old_msg_id = row.cleanup_msg_id
        row.cleanup_msg_id = new_msg_id
        if old_msg_id is not None and old_msg_id != new_msg_id:
            cancel_delayed_message(lightweight_maintenance_queue.name, old_msg_id)


def schedule_all_pending_cleanups() -> None:
    """Schedule a cleanup for every row in the registry.

    Past-due rows fire immediately. Future rows fire at their ``expires_at``.
    Called once at server startup so a restart never leaves rows orphaned.

    Orphan messages from a previous process are not tracked (their
    msg_ids weren't persisted) — they'll fire later, find the row's
    ``cleanup_msg_id`` no longer matches, and run the delete or no-op
    via the stale check. From boot forward the per-row cancel-on-rewrite
    invariant holds.
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
        schedule_cleanup_for_natural_key(
            user_id=r.user_id,
            agent_name=r.agent_name,
            expires_at=r.expires_at,
        )
    log.info(
        "agent_activity startup scan: scheduled %d immediate + %d future cleanups",
        n_immediate, n_future,
    )


def _parse_eta(expires_at: str) -> datetime:
    """Parse the stored ISO timestamp into a UTC-aware datetime."""
    return datetime.fromisoformat(expires_at)
