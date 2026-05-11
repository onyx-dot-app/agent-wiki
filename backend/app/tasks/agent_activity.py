"""Cleanup tasks for the agent-activity registry.

Each row in the ``agent_activity`` table carries an ``expires_at``.
When that moment passes, the row should be deleted. We don't want to
poll — instead, every time a row is upserted we schedule a delayed
task at exactly ``expires_at``, and on server restart we re-schedule
the same for every active row.

Why ``lightweight_maintenance_queue``: the cleanup is a single
``DELETE`` against ``agent_activity`` — sub-second, no LLM, no external
HTTP, no wiki commits. That matches the queue's placement rule, and
co-locating with BM25 reindex means a flood of trigger evals can't
delay an expiration cleanup (the prior wart, when this task lived on
``triggers_queue``).

Re-registration cancels the prior scheduled cleanup: each row stores
the pgmq ``msg_id`` of its current scheduled fire in
``cleanup_msg_id``. When ``upsert_activity`` slides ``expires_at``
forward, ``schedule_cleanup_for_natural_key`` enqueues a fresh task,
``pgmq.delete``s the old msg_id, and writes the new id back — all in a
single transaction. Without this, every re-read would leave the prior
delayed message orphaned in pgmq for the full TTL.

A cleanup is "stale" when the row has already been re-registered with
a later ``expires_at`` (the cancel may have raced the read). The
``expected_expires_at`` check below skips that fire as a no-op.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import AgentActivity
from app.db.session import session
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

    Two-step swap:

    1. ``cleanup_expired_activity.schedule(...)`` enqueues a fresh
       delayed message (cap-checked, gets back the new ``msg_id``).
    2. In one transaction: read the row's current ``cleanup_msg_id``,
       overwrite it with the new id, and ``pgmq.delete`` the old.

    The two steps aren't a single transaction — if the process dies
    between them, we leak the new msg_id (it'll fire and no-op via the
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
            _pgmq_delete(lightweight_maintenance_queue.name, new_msg_id)
            return
        old_msg_id = row.cleanup_msg_id
        row.cleanup_msg_id = new_msg_id
        if old_msg_id is not None and old_msg_id != new_msg_id:
            _pgmq_delete(lightweight_maintenance_queue.name, old_msg_id, sql_session=s)


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


def _pgmq_delete(queue_name: str, msg_id: int, *, sql_session: Session | None = None) -> None:
    """Delete a message by id. ``pgmq.delete`` returns false when the
    message is already gone (already fired or archived) — log and move on.

    Pass ``sql_session`` to participate in an existing transaction so the
    delete commits with the row update; otherwise opens its own session.
    """
    sql = text("SELECT pgmq.delete(:q, CAST(:m AS bigint))")
    params = {"q": queue_name, "m": msg_id}
    try:
        if sql_session is not None:
            ok = sql_session.execute(sql, params).scalar()
        else:
            with session() as s:
                ok = s.execute(sql, params).scalar()
    except Exception:
        # Failure here means we leak one orphan; the stale check at fire
        # time keeps it correct, just adds noise. Don't propagate.
        log.exception(
            "agent_activity cleanup cancel: pgmq.delete failed queue=%s msg=%s",
            queue_name, msg_id,
        )
        return
    if not ok:
        log.debug(
            "agent_activity cleanup cancel: msg %s already gone in queue %s",
            msg_id, queue_name,
        )


def _parse_eta(expires_at: str) -> datetime:
    """Parse the stored ISO timestamp into a UTC-aware datetime.

    The queue's enqueue path converts an ``eta`` (timezone-aware datetime)
    into a pgmq delay in seconds; passing aware UTC keeps the math right
    regardless of where the worker process happens to run.
    """
    return datetime.fromisoformat(expires_at)
