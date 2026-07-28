"""Startup doc-index backfill (``backfill_index_if_empty``).

A first boot over pre-existing wiki content skips seeding — the only other
boot path that indexes — so the lifespan backfills the doc index when it's
empty. Steady-state boots (non-zero count) and unreachable OpenSearch
(count None) both skip the O(pages) walk.
"""
from __future__ import annotations

import pytest

from app.db import fts
from app.tasks import reindex
from app.wiki import git as wiki_git
from tests.conftest import needs_opensearch


def _boom() -> None:
    raise AssertionError("reindex_all_inline should not run")


@needs_opensearch
def test_backfill_indexes_preexisting_pages(tmp_repo):
    """Pages committed before boot (no index entries) become searchable."""
    wiki_git.commit_file(
        "guides/db.md", "# DB Guide\nconnection pool sizing\n", "seed", author=None
    )
    wiki_git.commit_file(
        "runbooks/deploy.md", "# Deploy Runbook\nhelm upgrade steps\n", "seed", author=None
    )
    assert fts.count_documents() == 0

    reindex.backfill_index_if_empty()

    assert fts.count_documents() == 2
    hits = fts.search("connection pool", is_admin=True)
    assert [h.path for h in hits] == ["guides/db.md"]


def test_backfill_skips_when_index_nonempty(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(reindex.fts, "count_documents", lambda: 3)
    monkeypatch.setattr(reindex, "reindex_all_inline", _boom)

    reindex.backfill_index_if_empty()


def test_backfill_skips_when_opensearch_unreachable(monkeypatch: pytest.MonkeyPatch):
    """count None (client down) — indexing would fail too, so don't try."""
    monkeypatch.setattr(reindex.fts, "count_documents", lambda: None)
    monkeypatch.setattr(reindex, "reindex_all_inline", _boom)

    reindex.backfill_index_if_empty()
