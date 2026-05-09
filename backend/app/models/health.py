"""HTTP shapes for /api/health."""
from __future__ import annotations

from pydantic import BaseModel


class QueueHealth(BaseModel):
    name: str
    size: int | None
    limit: int
    ok: bool
    error: str | None


class HealthResponse(BaseModel):
    status: str  # "ok" | "degraded"
    queues: list[QueueHealth]
