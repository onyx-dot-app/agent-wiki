"""Startup doc-index backfill (``backfill_unindexed_pages``).

A first boot over pre-existing wiki content skips seeding — the only other
boot path that indexes — so the lifespan backfills whatever tracked pages
the doc index is missing. Steady-state boots (nothing missing) index
nothing, and unreachable OpenSearch (count None) skips the walk entirely.
"""
from __future__ import annotations

import pytest

from app.db import fts
from app.tasks import reindex
from app.wiki import git as wiki_git
from tests.conftest import needs_opensearch


def _recording_indexer(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Route ``backfill_unindexed_pages``'s per-page indexing through a
    recorder so tests can assert exactly which pages it touched."""
    calls: list[str] = []
    real = reindex.index_path_inline

    def record(path: str) -> None:
        calls.append(path)
        real(path)

    monkeypatch.setattr(reindex, "index_path_inline", record)
    return calls


@needs_opensearch
def test_backfill_indexes_preexisting_pages(tmp_repo):
    """Pages committed before boot (empty index) become searchable."""
    wiki_git.commit_file(
        "guides/db.md", "# DB Guide\nconnection pool sizing\n", "seed", author=None
    )
    wiki_git.commit_file(
        "runbooks/deploy.md", "# Deploy Runbook\nhelm upgrade steps\n", "seed", author=None
    )
    assert fts.count_documents() == 0

    reindex.backfill_unindexed_pages()

    assert fts.count_documents() == 2
    hits = fts.search("connection pool", is_admin=True)
    assert [h.path for h in hits] == ["guides/db.md"]


@needs_opensearch
def test_backfill_indexes_only_missing_pages(tmp_repo, monkeypatch: pytest.MonkeyPatch):
    """A partially populated index (nonzero count, some pages absent) gets
    exactly the absent pages indexed — not skipped, not fully re-walked."""
    wiki_git.commit_file("indexed.md", "# Indexed\n", "seed", author=None)
    wiki_git.commit_file("missing.md", "# Missing\nunsearchable so far\n", "seed", author=None)
    reindex.index_path_inline("indexed.md")
    assert fts.count_documents() == 1

    calls = _recording_indexer(monkeypatch)
    reindex.backfill_unindexed_pages()

    assert calls == ["missing.md"]
    assert fts.count_documents() == 2


@needs_opensearch
def test_backfill_noops_when_index_complete(tmp_repo, monkeypatch: pytest.MonkeyPatch):
    wiki_git.commit_file("guide.md", "# Guide\n", "seed", author=None)
    reindex.index_path_inline("guide.md")

    calls = _recording_indexer(monkeypatch)
    reindex.backfill_unindexed_pages()

    assert calls == []


def test_backfill_skips_when_opensearch_unreachable(monkeypatch: pytest.MonkeyPatch):
    """count None (client down) — indexing would fail too, so don't try."""
    monkeypatch.setattr(reindex.fts, "count_documents", lambda: None)

    def boom(*_args: object) -> None:
        raise AssertionError("nothing past the count check should run")

    monkeypatch.setattr(reindex.fts, "paths_under", boom)
    monkeypatch.setattr(reindex, "index_path_inline", boom)

    reindex.backfill_unindexed_pages()
