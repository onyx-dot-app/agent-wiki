"""Schedule-kind trigger evaluation. Driven by ``app/tasks/periodic.py``.

The selection logic lives in ``app.triggers.engine.find_due_schedule_triggers``
— this module is a thin wrapper kept so callers that already import
``app.triggers.time_based.due_triggers`` don't have to switch.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.triggers.engine import TriggerRecord, find_due_schedule_triggers


def due_triggers(now: datetime | None = None) -> list[TriggerRecord]:
    """Return enabled schedule triggers whose next cron fire is ≤ ``now``."""
    return find_due_schedule_triggers(now or datetime.now(timezone.utc))
