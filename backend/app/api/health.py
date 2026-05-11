"""FastAPI version of ``app/api/health.py``.

Phase 1 of the Flask→FastAPI migration. The route logic is identical;
only the framework wiring (``APIRouter`` + return-the-model vs
``Blueprint`` + ``jsonify(model.model_dump())``) differs. Both versions
coexist until Phase 5, when this file is renamed back to ``health.py``
and the Flask blueprint is deleted.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from app.models.health import HealthResponse, QueueHealth
from app.tasks.queues import QUEUES

router = APIRouter()
log = logging.getLogger(__name__)


def _build() -> HealthResponse:
    queues: list[QueueHealth] = []
    for name, queue in QUEUES.items():
        try:
            depth = queue.depth()
            ok = True
            error: str | None = None
            ready: int | None = depth.ready
            delayed: int | None = depth.delayed
            in_flight: int | None = depth.in_flight
        except Exception as e:  # noqa: BLE001 — surface as a per-queue error
            log.exception("depth() failed for %s", name)
            ready = delayed = in_flight = None
            ok = False
            error = str(e)
        queues.append(QueueHealth(
            name=name,
            ready=ready,
            delayed=delayed,
            in_flight=in_flight,
            limit=queue.max_size,
            ok=ok,
            error=error,
        ))
    overall_ok = all(q.ok for q in queues)
    return HealthResponse(
        status="ok" if overall_ok else "degraded", queues=queues,
    )


@router.get("", response_model=HealthResponse)
def health() -> HealthResponse:
    return _build()


@router.get("/", response_model=HealthResponse, include_in_schema=False)
def health_trailing() -> HealthResponse:
    return _build()
