"""Opt-in logging of ingest candidate decisions to the ingest_eval_samples table.

Enabled via INGEST_EVAL_LOGGING=true. Each row captures the source document, a
candidate wiki page, its title-boosted BM25 score, the unified diff of any edit,
and the outcome — one row per (document, candidate page) pair. Besides the
reconciler verdicts (committed/no_change/irrelevant) it also records
pre-reconciler drops: ``filtered_by_selector`` (weak-model selector),
``filtered_by_bm25_score`` (below the BM25 score threshold), and
``filtered_by_search_rank`` (a real search hit that fell outside the top-N
candidate cap — only captured when eval logging is on, since it widens the
search fetch). Intended for human review and building a regression test suite.
"""
from __future__ import annotations

import logging
from typing import Literal

from sqlalchemy import delete, select

from app.db.models import IngestEvalSample
from app.db.session import session
from app.wiki import git as wiki_git

log = logging.getLogger(__name__)

# Max rows a single retention sweep deletes — keeps the lightweight-maintenance
# DELETE bounded and quick. The daily schedule drains any larger backlog over
# successive runs.
RETENTION_BATCH = 50_000


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
        "filtered_by_search_rank",
    ],
    bm25_score: float | None,
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
            bm25_score=bm25_score,
            commit_sha=commit_sha,
        ))


def delete_older_than(cutoff: str, *, limit: int = RETENTION_BATCH) -> int:
    """Delete up to ``limit`` rows whose ``created_at`` is before ``cutoff``.

    ``cutoff`` is a ``YYYY-MM-DD HH:MM:SS`` UTC string — the same fixed-width
    format the column stores — so the lexicographic ``<`` comparison is also
    chronological and rides the ``created_at`` index. Bounded by ``limit`` so a
    single sweep stays quick even against a backlog; callers run it on a
    schedule that drains the rest. Returns the number of rows deleted.
    """
    with session() as s:
        ids = s.scalars(
            select(IngestEvalSample.id)
            .where(IngestEvalSample.created_at < cutoff)
            .order_by(IngestEvalSample.created_at)
            .limit(limit)
        ).all()
        if not ids:
            return 0
        s.execute(delete(IngestEvalSample).where(IngestEvalSample.id.in_(ids)))
        return len(ids)
