"""Reindex a single wiki doc into the FTS5 / BM25 search index.

Tasks in this module run on the ``wiki_bm25_huey`` queue — the cheap,
LLM-free queue dedicated to keeping the search index in sync with the git
working tree. Triggered after every wiki write (human edit, agent edit, move,
doc-updater commit) and on demand via ``POST /api/documents/reindex``.

See ``app/tasks/huey_app.py`` for the queue rationale.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.db import fts
from app.tasks.huey_app import wiki_bm25_huey
from app.wiki import git

log = logging.getLogger(__name__)


def _derive_title(path: str, body: str) -> str:
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return Path(path).stem


def reindex_path_inline(path: str) -> None:
    """Reindex a single wiki path into the FTS5 index, synchronously.

    Use this from startup bootstrap or anywhere that can't depend on a Huey
    worker being available. Online write paths should call the `reindex_path`
    task wrapper instead so they don't block the request.
    """
    log.debug("reindex %s", path)
    body = git.read_file(path)
    fts.upsert_document(doc_id=path, path=path, title=_derive_title(path, body), body=body)


@wiki_bm25_huey.task()
def reindex_document(doc_id: str, path: str, title: str) -> None:
    log.debug("reindex_document doc_id=%s path=%s", doc_id, path)
    body = git.read_file(path)
    fts.upsert_document(doc_id=doc_id, path=path, title=title, body=body)


@wiki_bm25_huey.task()
def reindex_path(path: str) -> None:
    """Reindex a wiki path into the FTS5 index. Path doubles as the doc_id."""
    reindex_path_inline(path)
