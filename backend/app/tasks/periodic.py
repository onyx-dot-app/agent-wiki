"""Time-based checks. The in-process scheduler in
``app/tasks/queue.py:run_consumer`` invokes these on a crontab.

``evaluate_scheduled_triggers`` lives on ``triggers_queue`` —
schedule-kind triggers are still trigger evaluation, just ignited by the
clock instead of a doc commit. Same evaluator engine, same event-log
output; keeping it on the triggers queue keeps trigger throughput
accountable to one consumer.

The task body delegates to ``app.tasks.triggers.evaluate_due_schedule_triggers``
which finds every schedule trigger whose next cron fire is at or before
``now`` (in the trigger's timezone), runs the same NL gate the delta
path uses (against a wiki-snapshot payload), and records ``trigger.fire``
events on match. ``schedule_last_fired_at`` is advanced for every
trigger we evaluated — match or no-match — so croniter's next pass
sees the right window.

See ``app/tasks/queues.py`` for the queue rationale.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.tasks.queue import crontab
from app.tasks.queues import triggers_queue
from app.tasks.triggers import evaluate_due_schedule_triggers

log = logging.getLogger(__name__)


# Every-minute scan: a no-op pass is one indexed query, and per-minute
# ticks keep a trigger's fire within ~1min of its cron time.
@triggers_queue.periodic_task(crontab())
def evaluate_scheduled_triggers() -> None:
    now = datetime.now(timezone.utc)
    evaluate_due_schedule_triggers(now)
