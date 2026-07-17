"""Wiki Auto Management detection tasks — bound to the dedicated ``detection``
queue (its own worker; see ``app/tasks/queues.py`` for why it isn't a
co-tenant of any other queue).

Emit-only: a sweep detects and writes ``change_proposals``; it never mutates
the wiki. Execution happens later, on approval.
"""
from __future__ import annotations

import logging

from app.tasks.queues import detection_queue
from app.wiki.automanage import executor, runner

log = logging.getLogger(__name__)


@detection_queue.task()
def run_detection_sweep(triggered_by_user_id: str | None) -> None:
    """Whole-space detection sweep. ``triggered_by_user_id`` is the admin who
    kicked it off (NULL for a system/scheduled run)."""
    runner.run_sweep(triggered_by_user_id=triggered_by_user_id)


@detection_queue.task()
def execute_proposal(proposal_id: int) -> None:
    """Apply an approved proposal (commits to git — hence the detection queue,
    not a co-tenant)."""
    executor.execute(proposal_id)
