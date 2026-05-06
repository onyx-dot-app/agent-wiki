"""Time-based checks. Huey ``periodic_task`` invokes these on a crontab.

Schedule-kind triggers are fanned out from here.
"""
from __future__ import annotations

from huey import crontab

from app.tasks.huey_app import huey


@huey.periodic_task(crontab(minute="*/5"))
def evaluate_scheduled_triggers() -> None:
    # TODO: load enabled schedule triggers due now, dispatch each to the
    # trigger engine. Record events.
    raise NotImplementedError


@huey.periodic_task(crontab(minute="0", hour="*/6"))
def stale_doc_review() -> None:
    # Optional: have the LLM nudge docs that haven't been touched in a while.
    raise NotImplementedError
