"""Time-based checks. Huey ``periodic_task`` invokes these on a crontab.

Schedule-kind triggers are fanned out from here.
"""
from __future__ import annotations

import logging

from huey import crontab

from app.tasks.huey_app import huey

log = logging.getLogger(__name__)


@huey.periodic_task(crontab(minute="*/5"))
def evaluate_scheduled_triggers() -> None:
    log.info("evaluate_scheduled_triggers tick")
    # TODO: load enabled schedule triggers due now, dispatch each to the
    # trigger engine. Record events.
    raise NotImplementedError


@huey.periodic_task(crontab(minute="0", hour="*/6"))
def stale_doc_review() -> None:
    log.info("stale_doc_review tick")
    # Optional: have the LLM nudge docs that haven't been touched in a while.
    raise NotImplementedError
