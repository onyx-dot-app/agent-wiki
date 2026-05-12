"""Tests for the seed-once contract in ``app.wiki.seed``.

Covers the three states ``seed_if_empty`` has to handle:

* fresh DB + empty wiki  → seed runs, marker stamped
* fresh DB + existing wiki content → marker stamped, seed skipped
* marker already set → seed skipped regardless of wiki state

The marker is what makes the seed safe against "user deletes every
onboarding page then reboots" — the bundled pages must not come back.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _list_md_paths():
    from app.wiki.git import list_paths

    return [p for p in list_paths() if p.endswith(".md")]


def _read_marker():
    from app.wiki.seed import _read_seed_marker

    return _read_seed_marker()


def test_seed_if_empty_writes_pages_and_stamps_marker(tmp_repo):
    from app.wiki.seed import iter_seed_pages, seed_if_empty

    expected_paths = [rel for rel, _ in iter_seed_pages()]
    # Sanity: the bundled seed exists in the test env (running from
    # `backend/`, so SEED_SOURCE_DIR resolves to `backend/wiki_seed/`).
    if not expected_paths:
        pytest.skip("no bundled wiki seed available in this environment")
    assert _read_marker() is None
    assert _list_md_paths() == []

    seeded = seed_if_empty(tmp_repo.wiki_dir)

    assert seeded is True
    assert sorted(_list_md_paths()) == sorted(expected_paths)
    assert _read_marker() is not None


def test_seed_if_empty_is_idempotent_when_marker_set(tmp_repo):
    """Second call must skip without writing anything new."""
    from app.wiki.seed import seed_if_empty

    if not seed_if_empty(tmp_repo.wiki_dir):
        pytest.skip("no bundled wiki seed available in this environment")

    pages_after_first = sorted(_list_md_paths())
    marker_after_first = _read_marker()

    second = seed_if_empty(tmp_repo.wiki_dir)

    assert second is False
    assert sorted(_list_md_paths()) == pages_after_first
    # Marker is not re-stamped on a no-op call.
    assert _read_marker() == marker_after_first


def test_seed_does_not_run_again_after_user_wipes_wiki(tmp_repo):
    """The core promise: emptying the wiki and re-firing the hook
    must NOT bring the onboarding pages back."""
    import shutil

    from app.wiki.git import ensure_wiki_repo
    from app.wiki.seed import seed_if_empty

    if not seed_if_empty(tmp_repo.wiki_dir):
        pytest.skip("no bundled wiki seed available in this environment")
    assert _list_md_paths(), "seed should have produced pages"

    # Simulate the user manually wiping their wiki directory: every
    # page + the .git itself.
    shutil.rmtree(tmp_repo.wiki_dir)
    Path(tmp_repo.wiki_dir).mkdir()
    ensure_wiki_repo()
    assert _list_md_paths() == []

    re_seeded = seed_if_empty(tmp_repo.wiki_dir)

    assert re_seeded is False
    assert _list_md_paths() == []


def test_pre_existing_wiki_content_just_stamps_marker(tmp_repo):
    """If an admin already has content (migration from a pre-seed
    install), the hook should stamp the marker without writing
    anything — so future deletions stay deleted."""
    from app.wiki.git import commit_file
    from app.wiki.seed import seed_if_empty

    commit_file("hand-written.md", "# Already here\n", "user create", author=None)
    assert _read_marker() is None

    seeded = seed_if_empty(tmp_repo.wiki_dir)

    assert seeded is False
    assert _list_md_paths() == ["hand-written.md"]
    assert _read_marker() is not None
