"""HTTP shapes for /api/health."""
from __future__ import annotations

from pydantic import BaseModel


class QueueHealth(BaseModel):
    name: str
    # Human name + what a backlog on this queue means for a person, from
    # the queue's own definition (``app/tasks/queues.py``).
    label: str
    description: str
    # Per-state breakdown. ``ready`` is what a worker can pick up right
    # now; ``delayed`` is messages scheduled for a future fire time
    # (``schedule(..., eta=...)``); ``in_flight`` is currently held by a
    # worker. ``ready + delayed`` is the figure the cap gates on. All
    # three are ``None`` when the per-queue read failed (see ``error``).
    ready: int | None
    delayed: int | None
    in_flight: int | None
    limit: int
    ok: bool
    error: str | None


class HealthResponse(BaseModel):
    status: str  # "ok" | "degraded"
    queues: list[QueueHealth]
