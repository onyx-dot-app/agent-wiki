"""Opt-in logging of ingest candidate decisions to the ingest_eval_samples table.

Enabled via INGEST_EVAL_LOGGING=true. Each row captures the source document, a
candidate wiki page, the unified diff of any edit, and the outcome — one row per
(document, candidate page) pair. Besides the reconciler verdicts
(committed/no_change/irrelevant) it also records pre-reconciler drops:
``filtered_by_selector`` (weak-model selector) and ``filtered_by_bm25_score``
(below the BM25 score threshold). Intended for human review and building a
regression test suite.
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
    source_document_id: str | None,
    source_type: str | None,
    source_title: str | None,
    source_url: str | None,
    source_content: str,
    wiki_path: str,
    wiki_body_before: str,
    outcome: Literal[
        "committed",
        "no_change",
        "irrelevant",
        "filtered_by_selector",
        "filtered_by_bm25_score",
    ],
    commit_sha: str | None,
) -> None:
    diff = wiki_git.diff_for_commit(commit_sha, wiki_path) if commit_sha is not None else None
    with session() as s:
        s.add(IngestEvalSample(
            source_document_id=source_document_id,
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
