"""Periodic sweep for expired MCP sessions.

Runs on ``lightweight_maintenance_queue`` — sub-second, no LLM, no
external HTTP, no wiki commits.

Deletes ``mcp_sessions`` rows whose ``expires_at`` is in the past.
The ``ON DELETE CASCADE`` FKs on ``mcp_path_subscriptions`` /
``mcp_job_subscriptions`` clean up the subscription rows in the same
transaction.
"""

from __future__ import annotations

import logging

from app.mcp_server import session as mcp_session
from app.tasks.queue import crontab
from app.tasks.queues import lightweight_maintenance_queue

log = logging.getLogger(__name__)


@lightweight_maintenance_queue.periodic_task(crontab(minute="*/15"))
def reap_expired_mcp_sessions() -> None:
    count = mcp_session.reap_expired()
    if count:
        log.info("mcp_session_cleanup reaped=%d", count)
