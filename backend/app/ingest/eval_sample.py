"""Opt-in logging of ingest reconciler decisions to the ingest_eval_samples table.

Enabled via INGEST_EXAMPLE_LOGGING=true. Each row captures the source document,
the wiki page before reconciliation, the unified diff of any edit, and the outcome.
Intended for human review and building a regression test suite.
"""
from __future__ import annotations

import logging
from typing import Literal

from app.db.models import IngestEvalSample
from app.db.session import session
from app.wiki import git as wiki_git

log = logging.getLogger(__name__)


def log_sample(
    *,
    source_type: str | None,
    source_title: str | None,
    source_url: str | None,
    source_content: str,
    wiki_path: str,
    wiki_body_before: str,
    outcome: Literal["committed", "no_change"],
    commit_sha: str | None,
) -> None:
    diff = wiki_git.diff_for_commit(commit_sha, wiki_path) if commit_sha is not None else None
    with session() as s:
        s.add(IngestEvalSample(
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
