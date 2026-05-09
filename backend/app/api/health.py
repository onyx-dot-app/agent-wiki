"""Healthcheck endpoint.

Reports liveness of the Flask process plus the current backlog of each
pgmq queue against its configured cap (``MAX_QUEUE_SIZE``). Exposed so
the frontend ``/health`` page (and any external probe) can poll the
backlog without hitting Postgres directly.

The queue size read goes through ``TaskQueue.size()`` — a filtered
``count(*)`` against ``pgmq.q_<name>`` that excludes in-flight
messages (a worker has read them and the VT hasn't expired yet) so
"size" tracks backlog the producer can do something about. Cheap
enough to hit on every poll.
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
            size: int | None = queue.size()
            ok = True
            error: str | None = None
        except Exception as e:  # noqa: BLE001 — surface as a per-queue error
            log.exception("size() failed for %s", name)
            size = None
            ok = False
            error = str(e)
        queues.append(QueueHealth(
            name=name, size=size, limit=queue.max_size, ok=ok, error=error,
        ))

    overall_ok = all(q.ok for q in queues)
    return jsonify(HealthResponse(
        status="ok" if overall_ok else "degraded", queues=queues,
    ).model_dump())
