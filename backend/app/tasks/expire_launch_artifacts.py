"""Periodic sweep for launch codes + stale agent sessions.

Runs on ``lightweight_maintenance_queue`` — sub-second, no LLM, no
external HTTP, no wiki commits.

Three actions per tick:

1. Delete expired ``launch_codes`` rows.
2. Mark ``active`` sessions whose ``last_activity_at`` is older than
   ``CONFIG.agent_session_idle_seconds`` as ``idle``.
3. Mark ``idle`` sessions older than
   ``CONFIG.agent_session_close_after_idle_seconds`` as ``closed``.
4. — mark ``active`` sessions that never reported ``spawn_ok``
   within 30s as ``failed`` (helper crashed mid-spawn).
"""

from __future__ import annotations

import logging

from app.db import launch_codes as codes_repo
from app.db import agent_sessions as sessions_repo
from app.tasks.queue import crontab
from app.tasks.queues import lightweight_maintenance_queue

log = logging.getLogger(__name__)


@lightweight_maintenance_queue.periodic_task(crontab(minute="*"))
def expire_launch_artifacts() -> None:
    deleted = codes_repo.expire_sweep()
    idle = sessions_repo.mark_stale_idle()
    closed = sessions_repo.evict_idle_to_closed()
    spawn_missed = sessions_repo.evict_spawn_missed()
    if deleted or idle or closed or spawn_missed:
        log.info(
            "expire_launch_artifacts deleted=%d marked_idle=%d closed=%d spawn_missed=%d",
            deleted,
            idle,
            closed,
            spawn_missed,
        )
