"""Entry point for a Huey consumer container.

Run with: ``python -m app.tasks.run_worker <queue>`` where ``<queue>`` is one
of ``documents``, ``triggers``, ``wiki_doc_index``. Each queue gets its own
worker process — see ``app/tasks/huey_app.py`` for the queue rationale.

We import every task module up front (regardless of which queue we're
serving) so all ``@<huey>.task()`` decorators run and Huey's task registry
is populated. The consumer then only pulls from the queue it was launched
with; tasks bound to other queues are inert in this process.
"""
from __future__ import annotations

import argparse
import sys

# Importing modules registers tasks on their respective Huey instances.
from app.tasks import document_update, periodic, reindex, triggers  # noqa: F401
from app.tasks.huey_app import QUEUES
from app.utils.logging import setup_logging


# Per-queue worker counts. Indexer is cheap so we give it more headroom;
# documents is LLM-bound and we don't want to fan out concurrent provider
# calls from a single host. Tune as we get real load data.
_WORKERS = {
    "documents": 2,
    "triggers": 4,
    "wiki_doc_index": 4,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a Huey consumer for one queue.")
    parser.add_argument("queue", choices=sorted(QUEUES.keys()))
    args = parser.parse_args()

    setup_logging()
    huey = QUEUES[args.queue]
    workers = _WORKERS[args.queue]

    from huey.consumer import Consumer
    Consumer(huey, workers=workers, worker_type="thread").run()


if __name__ == "__main__":
    main()
