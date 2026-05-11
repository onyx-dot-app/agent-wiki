"""Hourly drift sweep for the BM25 index.

``reconcile_bm25_index`` is the safety net behind every wiki write: if
the post-commit ``reindex_path`` enqueue is ever lost (worker crash
mid-handler, dropped pgmq message, a future write path forgetting to
call ``after_doc_write``), this periodic task catches the drift on the
next cycle by comparing each tracked ``.md`` path's HEAD sha to
``DocumentFts.indexed_sha`` and enqueuing reindexes for mismatches.

We exercise the function body directly (``reconcile_bm25_index.fn()``)
under ``lightweight_maintenance_queue.immediate_mode()`` so the
``reindex_path`` calls it fans out run synchronously. The leader-elected
scheduler is out of scope for these tests — that's covered by
the queue-level integration tests in ``test_*queue*`` modules.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db import fts
from app.tasks import reindex
from app.tasks.queue import QueueFullError
from app.tasks.queues import lightweight_maintenance_queue
from app.tasks.reindex import (
    _read_reconcile_cursor,
    _write_reconcile_cursor,
    reconcile_bm25_index,
)
from app.wiki import git as wiki_git

from tests._seed import list_fts_rows


@pytest.fixture(autouse=True)
def _immediate_queue():
    """Run the ``reindex_path`` fan-out inline so we can assert on the
    resulting FTS state without spinning up a worker."""
    with lightweight_maintenance_queue.immediate_mode():
        yield


def _run_reconcile() -> None:
    """Bypass the queue and execute the periodic task's body directly.

    The scheduler thread normally enqueues this; here we just want the
    handler logic, so call the underlying function via ``Task.fn``.
    """
    reconcile_bm25_index.fn()


def _row_for(path: str) -> dict | None:
    for r in list_fts_rows():
        if r["path"] == path:
            return r
    return None


# --------------------------------------------------------------------------- #
# Bootstrap mode — first ever run, cursor is NULL                             #
# --------------------------------------------------------------------------- #


def test_bootstrap_indexes_docs_missed_by_post_commit_hook(tmp_repo):
    """A doc that was committed directly via ``wiki_git.commit_file``
    bypasses ``after_doc_write`` — the post-commit hook is the seam the
    reconciler exists to backstop."""
    wiki_git.commit_file("guide.md", "# Guide\n\nfindable\n", "seed", author=None)
    assert _row_for("guide.md") is None

    _run_reconcile()

    row = _row_for("guide.md")
    assert row is not None
    assert "findable" in row["body"]


def test_bootstrap_removes_orphan_fts_rows(tmp_repo):
    """A FTS row whose path is no longer in the repo (or never was) is
    cleaned up on the bootstrap pass."""
    fts.upsert_document(
        doc_id="ghost.md", path="ghost.md", title="Ghost", body="boo"
    )
    assert _row_for("ghost.md") is not None

    _run_reconcile()

    assert _row_for("ghost.md") is None


def test_bootstrap_stamps_indexed_sha_to_match_head(tmp_repo):
    wiki_git.commit_file("tracked.md", "# T\n\nbody\n", "seed", author=None)

    _run_reconcile()

    row = _row_for("tracked.md")
    assert row is not None
    assert row["indexed_sha"] == wiki_git.head_sha_for_path("tracked.md")


def test_reconcile_skips_non_md_files(tmp_repo):
    """Trigger YAMLs and ``.gitkeep`` shouldn't end up in FTS, even on
    bootstrap. The reconcile filter mirrors the production write path."""
    wiki_git.commit_file(
        "dir/.trigger_abc.yaml", "name: t\n", "seed", author=None
    )
    wiki_git.commit_file("dir/.gitkeep", "", "seed", author=None)

    _run_reconcile()

    paths = {r["path"] for r in list_fts_rows()}
    assert "dir/.trigger_abc.yaml" not in paths
    assert "dir/.gitkeep" not in paths


# --------------------------------------------------------------------------- #
# Cursor advancement                                                          #
# --------------------------------------------------------------------------- #


def test_cursor_advances_on_successful_completion(tmp_repo):
    assert _read_reconcile_cursor() is None

    before = datetime.now(timezone.utc)
    _run_reconcile()
    after = datetime.now(timezone.utc)

    cursor = _read_reconcile_cursor()
    assert cursor is not None
    # Cursor is stamped with ``started_at``, so it's in [before, after].
    assert before - timedelta(seconds=1) <= cursor <= after + timedelta(seconds=1)


def test_cursor_does_not_advance_when_queue_is_full(tmp_repo, monkeypatch):
    """If ``reindex_path`` raises ``QueueFullError`` mid-fan-out, the
    cursor must stay unchanged so the next run retries the same
    window."""
    wiki_git.commit_file("a.md", "# A\nbody\n", "seed", author=None)
    wiki_git.commit_file("b.md", "# B\nbody\n", "seed", author=None)

    def _explode(_path: str) -> int | None:
        raise QueueFullError("lightweight_maintenance", size=999, limit=999)

    monkeypatch.setattr(reindex, "reindex_path", _explode)

    _run_reconcile()

    assert _read_reconcile_cursor() is None


# --------------------------------------------------------------------------- #
# Windowed mode — subsequent runs scope work to recent activity               #
# --------------------------------------------------------------------------- #


def test_windowed_run_picks_up_drift_inside_window(tmp_repo):
    """Pre-set the cursor to an hour ago so a freshly committed doc
    falls inside the window."""
    _write_reconcile_cursor(datetime.now(timezone.utc) - timedelta(hours=1))
    wiki_git.commit_file("drift.md", "# Drift\n\nsearchable\n", "seed", author=None)

    _run_reconcile()

    assert _row_for("drift.md") is not None


def test_windowed_run_ignores_paths_outside_window(tmp_repo):
    """Cursor set to the future ⇒ ``git log --since=<future>`` returns
    nothing, so no candidates and no work."""
    wiki_git.commit_file(
        "untouched.md", "# Untouched\n\nbody\n", "seed", author=None
    )
    _write_reconcile_cursor(datetime.now(timezone.utc) + timedelta(days=1))

    _run_reconcile()

    assert _row_for("untouched.md") is None


def test_windowed_run_does_not_sweep_orphans_outside_scope(tmp_repo):
    """The wide orphan sweep only fires on bootstrap; in windowed mode
    a pre-existing orphan that wasn't touched in-window is intentionally
    left alone (the assumption is that a previous successful run already
    handled it)."""
    fts.upsert_document(
        doc_id="old.md", path="old.md", title="Old", body="ancient"
    )
    _write_reconcile_cursor(datetime.now(timezone.utc) - timedelta(seconds=1))

    _run_reconcile()

    assert _row_for("old.md") is not None
