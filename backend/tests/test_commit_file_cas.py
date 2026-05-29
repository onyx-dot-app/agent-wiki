"""Tests for the compare-and-swap (``expected_head``) guard in
``app.wiki.git.commit_file`` and the cross-process commit lock.

These run against a real on-disk git repo but touch **no database**, so they
exercise the actual ``flock`` + CAS path even when Postgres isn't available.
The shared test fixtures (``tmp_repo``) pull in a Postgres schema; here we
stand up a bare repo with a stub config instead.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from app.wiki import git as wiki_git


@pytest.fixture
def bare_repo(tmp_path, monkeypatch):
    """An initialized wiki git repo with no DB. Yields the repo path."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    monkeypatch.setattr(wiki_git, "CONFIG", SimpleNamespace(wiki_dir=str(wiki_dir)))
    wiki_git.ensure_wiki_repo()
    return wiki_dir


def test_cas_commits_when_head_matches(bare_repo):
    """Matching ``expected_head`` → the swap proceeds and HEAD advances."""
    base = wiki_git.commit_file("page.md", "v1\n", "create", author=None)

    new = wiki_git.commit_file("page.md", "v2\n", "edit", author=None, expected_head=base)

    assert new != base
    assert wiki_git.read_file("page.md") == "v2\n"
    assert wiki_git.head_sha_for_path("page.md") == new


def test_cas_raises_when_head_moved(bare_repo):
    """Stale ``expected_head`` → ``GitHeadMovedError``, commit aborted."""
    base = wiki_git.commit_file("page.md", "v1\n", "create", author=None)
    current = wiki_git.commit_file("page.md", "v2\n", "edit", author=None)
    assert current != base

    with pytest.raises(wiki_git.GitHeadMovedError) as exc:
        wiki_git.commit_file(
            "page.md", "stale body\n", "edit on stale base", author=None, expected_head=base
        )

    # The guard reports the real current head and leaves the page untouched.
    assert exc.value.current_sha == current
    assert exc.value.rel_path == "page.md"
    assert wiki_git.read_file("page.md") == "v2\n"
    assert wiki_git.head_sha_for_path("page.md") == current


def test_no_cas_commits_unconditionally(bare_repo):
    """``expected_head=None`` (create / no-base path) commits regardless."""
    wiki_git.commit_file("page.md", "v1\n", "create", author=None)
    new = wiki_git.commit_file("page.md", "v2\n", "edit", author=None)  # no expected_head

    assert wiki_git.read_file("page.md") == "v2\n"
    assert wiki_git.head_sha_for_path("page.md") == new


def test_concurrent_cas_lets_exactly_one_writer_win(bare_repo):
    """Real flock + CAS under threads: all writers share one base SHA, so
    exactly one commit lands and the rest see ``GitHeadMovedError``.

    Deterministic because every thread passes the *same* ``expected_head``:
    once the first writer commits, HEAD moves and no other stale-based commit
    can match. This proves the lock serializes the section and the CAS rejects
    writers whose base went stale while they waited on the lock.
    """
    base = wiki_git.commit_file("page.md", "base\n", "create", author=None)

    n = 16
    start = threading.Barrier(n)
    wins: list[str] = []
    moved: list[wiki_git.GitHeadMovedError] = []
    lock = threading.Lock()

    def writer(i: int) -> None:
        start.wait()  # release all threads at once to maximize contention
        try:
            sha = wiki_git.commit_file(
                "page.md", f"writer-{i}\n", f"edit {i}", author=None, expected_head=base
            )
            with lock:
                wins.append(sha)
        except wiki_git.GitHeadMovedError as e:
            with lock:
                moved.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one writer's commit lands; everyone else is rejected by the CAS.
    assert len(wins) == 1
    assert len(moved) == n - 1

    winner = wins[0]
    final = wiki_git.head_sha_for_path("page.md")
    assert final == winner
    # The repo isn't corrupted: the committed body is one writer's content.
    assert wiki_git.read_file("page.md").startswith("writer-")
    # Every rejected writer saw the winner as the current head.
    assert all(e.current_sha == winner for e in moved)
