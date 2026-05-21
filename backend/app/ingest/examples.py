"""Opt-in logging of ingest reconciler decisions to the ingest_examples table.

Enabled via INGEST_EXAMPLE_LOGGING=true. Each row captures the source document,
the wiki page before reconciliation, the unified diff of any edit, and the outcome.
Intended for human review and building a regression test suite.
"""
from __future__ import annotations

import difflib
import logging
from typing import Literal

from app.db.models import IngestExample
from app.db.session import session

log = logging.getLogger(__name__)


def make_diff(before: str, after: str) -> str:
    """Return a unified diff string between before and after."""
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="before",
            tofile="after",
        )
    )


def log_example(
    *,
    source_type: str | None,
    source_title: str | None,
    source_url: str | None,
    source_content: str,
    wiki_path: str,
    wiki_body_before: str,
    wiki_body_after: str | None,
    outcome: Literal["committed", "no_change"],
    commit_sha: str | None,
) -> None:
    diff = (make_diff(wiki_body_before, wiki_body_after) or None) if wiki_body_after is not None else None
    with session() as s:
        s.add(IngestExample(
            source_type=source_type,
            source_title=source_title,
            source_url=source_url,
            source_content=source_content,
            wiki_path=wiki_path,
            wiki_body_before=wiki_body_before,
            diff=diff,
            outcome=outcome,
            commit_sha=commit_sha,
        ))
