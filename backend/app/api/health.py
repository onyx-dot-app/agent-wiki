"""Healthcheck endpoint.

Reports liveness of the Flask process plus the current backlog of each
Huey queue against its configured cap (``MAX_QUEUE_SIZE``). Exposed so
the frontend ``/health`` page (and any external probe) can read the
backlog without shelling into the queue SQLite file.

The queue size read goes through ``storage.queue_size()`` — it's a
single ``SELECT COUNT(*)`` against the queue table, cheap enough to hit
on every poll.
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify

from app.tasks.huey_app import QUEUES

bp = Blueprint("health", __name__)
log = logging.getLogger(__name__)


@bp.get("")
@bp.get("/")
def health():
    queues = []
    for name, huey in QUEUES.items():
        try:
            size = huey.storage.queue_size()
            ok = True
            error = None
        except Exception as e:  # noqa: BLE001 — surface as a per-queue error
            log.exception("queue_size failed for %s", name)
            size = None
            ok = False
            error = str(e)
        queues.append({
            "name": name,
            "size": size,
            "limit": huey.max_queue_size,
            "ok": ok,
            "error": error,
        })

    overall_ok = all(q["ok"] for q in queues)
    return jsonify(status="ok" if overall_ok else "degraded", queues=queues)
