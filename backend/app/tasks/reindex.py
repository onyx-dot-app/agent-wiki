"""Reindex a single doc into the FTS index from the git working tree."""
from __future__ import annotations

from pathlib import Path

from app.db import fts
from app.tasks.huey_app import huey
from app.wiki import git


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
    body = git.read_file(path)
    fts.upsert_document(doc_id=path, path=path, title=_derive_title(path, body), body=body)


@huey.task()
def reindex_document(doc_id: str, path: str, title: str) -> None:
    body = git.read_file(path)
    fts.upsert_document(doc_id=doc_id, path=path, title=title, body=body)


@huey.task()
def reindex_path(path: str) -> None:
    """Reindex a wiki path into the FTS5 index. Path doubles as the doc_id."""
    reindex_path_inline(path)
