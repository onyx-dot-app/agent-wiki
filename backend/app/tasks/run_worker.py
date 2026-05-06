"""Entry point for the Huey consumer container.

Run with: ``python -m app.tasks.run_worker``
"""
from __future__ import annotations

# Importing modules registers tasks on the Huey instance.
from app.tasks import document_update, periodic, reindex  # noqa: F401
from app.tasks.huey_app import huey


def main() -> None:
    from huey.consumer import Consumer
    Consumer(huey, workers=2, worker_type="thread").run()


if __name__ == "__main__":
    main()
