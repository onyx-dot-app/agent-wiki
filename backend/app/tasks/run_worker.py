"""Entry point for a worker container.

Run with: ``python -m app.tasks.run_worker <queue>`` where ``<queue>`` is
one of ``documents``, ``triggers``, ``lightweight_maintenance``. Each
queue gets its own worker process — see ``app/tasks/queues.py`` for the
queue rationale.

We import every task module up front (regardless of which queue we're
serving) so all ``@<queue>.task()`` decorators run and the per-queue
handler registry is populated. The consumer then only pulls from the
queue it was launched with; tasks bound to other queues are inert in
this process.
"""
from __future__ import annotations

import argparse
import logging
import time

from sqlalchemy import text

# Importing modules registers tasks on their respective queues.
from app.tasks import agent_activity, chat_title, wiki_update, periodic, reindex, triggers  # noqa: F401  # pyright: ignore[reportUnusedImport]
from app.tasks.queues import QUEUES
from app.tasks.queue import run_consumer
from app.utils.logging import setup_logging

log = logging.getLogger(__name__)


def _wait_for_db(timeout_s: float = 60.0, poll_s: float = 1.0) -> None:
    """Block until the app schema exists (migrations have run).

    Workers start in parallel with the backend. Poll until the ``users``
    table exists so we know alembic has finished before we try to use
    the DB.
    """
    from app.db.session import session

    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with session() as s:
                ready = s.execute(
                    text(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_name = 'users'"
                    )
                ).scalar()
        except Exception:
            ready = None
        if ready:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"DB schema not available after {timeout_s:.0f}s — "
                "is the backend running its migrations?"
            )
        log.info("waiting for DB migrations …")
        time.sleep(poll_s)

# Per-queue handler concurrency (= number of worker threads in this process).
# ``documents`` is LLM-bound; we don't want concurrent provider calls from a
# single host so it stays at 1. The cheap queues run wider.
_CONCURRENCY = {
    "documents": 1,
    "triggers": 4,
    "lightweight_maintenance": 4,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a task-queue consumer for one queue.")
    parser.add_argument("queue", choices=sorted(QUEUES.keys()))
    args = parser.parse_args()

    setup_logging()
    _wait_for_db()
    queue = QUEUES[args.queue]
    concurrency = _CONCURRENCY[args.queue]

    run_consumer(queue, concurrency=concurrency)


if __name__ == "__main__":
    main()
