"""Schedule-kind trigger evaluation. Driven by tasks/periodic.py."""
from __future__ import annotations

from typing import Any


def due_triggers(now_iso: str) -> list[dict[str, Any]]:
    # TODO: load enabled schedule triggers whose cron matches ``now_iso``.
    raise NotImplementedError
