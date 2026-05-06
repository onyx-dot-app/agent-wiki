"""Reindex a single doc into the FTS index from the git working tree."""
from __future__ import annotations

from app.db import fts
from app.tasks.huey_app import huey
from app.wiki import git


@huey.task()
def reindex_document(doc_id: str, path: str, title: str) -> None:
    body = git.read_file(path)
    fts.upsert_document(doc_id=doc_id, path=path, title=title, body=body)
