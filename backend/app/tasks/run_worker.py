"""Entry point for a worker container.

Run with: ``python -m app.tasks.run_worker <queue> [<queue> ...]`` where each
``<queue>`` is one of ``documents``, ``triggers``, ``coedit``, ``automanage``,
or ``lightweight_maintenance``.

**Queues vs pods.** A queue is a Redis stream + its own consumer (a thread
pool); that's the *isolation* unit, and it's cheap. A worker **process/pod** is
the *memory* unit — it pays a ~185 MiB import-graph floor regardless of how many
queues it serves. So a single process can host several queues, each as its own
consumer (thread pool): backlogs stay isolated (separate streams) and run
concurrently (the work is I/O-bound, so the GIL is released during LLM/HTTP/DB/
git), while the memory floor is paid once. Pass several queue names to group
them into one process; pass one to give a queue its own process.

Deployment (see ``docker-compose.yml`` / helm ``worker.groups``) currently runs
two pods: ``coedit`` alone (the only real-time, user-visible-freshness path) and
a ``background`` pod hosting the rest.

We import every task module up front (regardless of which queues we're serving)
so all ``@<queue>.task()`` decorators run and the handler registry is populated.
Each consumer only pulls from its own queue; tasks bound to unserved queues are
inert in this process.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import threading
import time

from prometheus_client import start_http_server
from sqlalchemy import text

from app.config import verify_secret_key
from app.tasks.queues import QUEUES
from app.tasks.queue import install_signal_handlers, run_consumer
from app.utils.logging import setup_logging

_TASK_MODULES = (
    "app.tasks.agent_activity",
    "app.tasks.chat_title",
    "app.tasks.automanage",
    "app.tasks.coedit_checkpoint",
    "app.tasks.coedit_rebase",
    "app.tasks.craft",
    "app.tasks.wiki_update",
    "app.tasks.expire_launch_artifacts",
    "app.tasks.ingest_eval_retention",
    "app.tasks.mcp_session_cleanup",
    "app.tasks.notify_emails",
    "app.tasks.periodic",
    "app.tasks.reindex",
    "app.tasks.trash_purge",
    "app.tasks.triggers",
    "app.tasks.update_frequency",
)
for _mod in _TASK_MODULES:
    importlib.import_module(_mod)

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
                    text("SELECT 1 FROM information_schema.tables WHERE table_name = 'users'")
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
# single host so it stays at 1. The cheap queues run wider. ``coedit`` runs
# wider than ``documents`` even though it can commit + AI-merge: a checkpoint is
# mostly a fast git commit (the AI merge fires only on a concurrent-commit
# overlap), and per-session safety comes from a Postgres advisory lock, not
# single-threading — so distinct sessions checkpoint in parallel.
_CONCURRENCY = {
    "documents": 1,
    "triggers": 4,
    "coedit": 4,
    # Automanage: whole-space sweeps + proposal execution. Two concurrent sweeps
    # would just duplicate work, so serialize at 1; executes are safe under the
    # git commit lock.
    "automanage": 1,
    "lightweight_maintenance": 4,
}

# Per-queue Prometheus port. Distinct ports so multiple worker processes can run
# on the same host (local dev / launch.json compound) without binding the same
# socket. A process serving several queues binds one server (the lowest port of
# its queues); the collector reports depth for every queue regardless, so one
# server per process is enough. The helm chart derives each group's containerPort
# the same way.
_METRICS_PORT = {
    "documents": 9091,
    "triggers": 9092,
    "lightweight_maintenance": 9093,
    "coedit": 9094,
    "automanage": 9095,
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run task-queue consumers for one or more queues in this process."
    )
    parser.add_argument("queues", nargs="+", choices=sorted(QUEUES.keys()))
    args = parser.parse_args()

    setup_logging()
    verify_secret_key()
    # Install signal handlers once on the main thread; the per-queue consumers
    # run in threads (where install is a no-op) and all observe the same stop
    # event, so one SIGTERM drains every consumer in this process.
    install_signal_handlers()
    # Metrics are observability, not a hard dependency: a taken port (e.g.
    # another local stack on the same host) must not stop the worker consuming.
    port = min(_METRICS_PORT[q] for q in args.queues)
    try:
        start_http_server(port)
    except OSError as e:
        log.warning("metrics server not started on :%d (%s)", port, e)
    _wait_for_db()

    # One consumer (thread pool) per queue, each in its own thread; run_consumer
    # blocks until the stop event drains its pool, so we join them all.
    threads = [
        threading.Thread(
            target=run_consumer,
            args=(QUEUES[q],),
            kwargs={"concurrency": _CONCURRENCY[q]},
            name=f"consumer-{q}",
        )
        for q in args.queues
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
