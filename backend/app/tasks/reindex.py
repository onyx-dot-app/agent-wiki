"""Reindex stubs — search indexing is disabled until OpenSearch lands.

pg_textsearch has been removed. The task handlers are kept registered on
the lightweight_maintenance_queue so existing queue messages don't error
with "no handler" — they just become silent no-ops until OpenSearch
replaces this module.
"""
from __future__ import annotations

import logging

from app.tasks.queue import crontab
from app.tasks.queues import lightweight_maintenance_queue

log = logging.getLogger(__name__)


@lightweight_maintenance_queue.task()
def reindex_document(doc_id: str, path: str, title: str) -> None:
    pass


@lightweight_maintenance_queue.task()
def reindex_path(path: str) -> None:
    pass


def reindex_path_inline(path: str) -> None:
    pass


@lightweight_maintenance_queue.periodic_task(crontab(minute="0"))
def reconcile_bm25_index() -> None:
    pass
