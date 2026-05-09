"""Time-based checks. The in-process scheduler in
``app/tasks/queue.py:run_consumer`` invokes these on a crontab.

``evaluate_scheduled_triggers`` lives on ``triggers_queue`` —
schedule-kind triggers are still trigger evaluation, just ignited by the
clock instead of a doc commit. Same evaluator, same code paths, same
event-log output; keeping it on the triggers queue keeps trigger
throughput accountable to one consumer.

See ``app/tasks/queues.py`` for the queue rationale.
"""
from __future__ import annotations

import logging

from app.tasks.queues import triggers_queue
from app.tasks.queue import crontab

log = logging.getLogger(__name__)


@triggers_queue.periodic_task(crontab(minute="*/5"))
def evaluate_scheduled_triggers() -> None:
    log.info("evaluate_scheduled_triggers tick")
    # TODO: load enabled schedule triggers due now (via
    # app.triggers.time_based.due_triggers), dispatch each to the trigger
    # engine, and write trigger.fire events on match.
    raise NotImplementedError
