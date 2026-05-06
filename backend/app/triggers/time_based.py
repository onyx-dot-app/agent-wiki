"""Schedule-kind trigger evaluation. Driven by tasks/periodic.py."""
from __future__ import annotations


def due_triggers(now_iso: str) -> list[dict]:
    # TODO: load enabled schedule triggers whose cron matches ``now_iso``.
    raise NotImplementedError
