"""Healthcheck endpoint.

Reports liveness of the Flask process plus the current backlog of each
pgmq queue against its configured cap (``MAX_QUEUE_SIZE``). Exposed so
the frontend ``/health`` page (and any external probe) can poll the
backlog without hitting Postgres directly.

The per-queue read goes through ``TaskQueue.depth()`` — one filtered
``count(*)`` against ``pgmq.q_<name>`` that splits messages into
``ready`` / ``delayed`` / ``in_flight``. The split matters: a queue
sitting at "size 9" because of nine ``schedule(..., eta=tomorrow)``
fires is healthy; the same number from ready messages no consumer is
draining is not. Cheap enough to hit on every poll.
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify

from app.models.health import HealthResponse, QueueHealth
from app.tasks.queues import QUEUES

bp = Blueprint("health", __name__)
log = logging.getLogger(__name__)


@bp.get("")
@bp.get("/")
def health():
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
    return jsonify(HealthResponse(
        status="ok" if overall_ok else "degraded", queues=queues,
    ).model_dump())
