"""Unit tests for ``commit_with_retry`` in ``app.wiki.git``.

``commit_with_retry`` is the git-layer commit entry point for the non-agent
callers (human edits, ingest, folder/trigger writes). It always retries the
ref-lock race; when ``base_body`` is supplied it also 3-way merges a concurrent
edit. The agent path's LLM-merge loop ``commit_with_ai_rebase`` is covered
separately in ``test_commit_with_ai_rebase.py``.

All git/filesystem I/O is monkeypatched so the tests run without a real repo
or database.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.wiki import git as wiki_git
from app.wiki.git import (
    CommitMaxRetriesError,
    GitCommitLockError,
    GitMergeConflictError,
    MergeResult,
)

_PATH = "docs/page.md"
_MSG = "update page"
_BASE = "base content\n"
_NEW = "new content\n"
_SHA_A = "aaaa1111"
_SHA_B = "bbbb2222"
_SHA_C = "cccc3333"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Don't actually sleep between lock-race retries."""
    monkeypatch.setattr("app.wiki.git.time.sleep", lambda *_a, **_k: None)


# ---------------------------------------------------------------------------
# base_body=None — no merge, lock-only retry
# ---------------------------------------------------------------------------


def test_no_base_passes_through(monkeypatch):
    commit_file = MagicMock(return_value=_SHA_A)
    monkeypatch.setattr("app.wiki.git.commit_file", commit_file)
    monkeypatch.setattr("app.wiki.git.head_sha_for_path", lambda _p: _SHA_A)
    # _read_worktree must never be consulted when there's no base.
    monkeypatch.setattr(
        "app.wiki.git._read_worktree",
        MagicMock(side_effect=AssertionError("should not read worktree without a base")),
    )

    sha, body = wiki_git.commit_with_retry(_PATH, new_body="body", message=_MSG)

    assert sha == _SHA_A
    assert body == "body"
    commit_file.assert_called_once_with(_PATH, "body", _MSG, author=None)


def test_no_base_retries_lock_then_succeeds(monkeypatch):
    commit_file = MagicMock(side_effect=[GitCommitLockError(_PATH), _SHA_B])
    monkeypatch.setattr("app.wiki.git.commit_file", commit_file)
    monkeypatch.setattr("app.wiki.git.head_sha_for_path", lambda _p: _SHA_A)

    sha, _ = wiki_git.commit_with_retry(_PATH, new_body="body", message=_MSG, max_retries=2)

    assert sha == _SHA_B
    assert commit_file.call_count == 2


def test_no_base_raises_after_max_lock_retries(monkeypatch):
    commit_file = MagicMock(side_effect=GitCommitLockError(_PATH))
    monkeypatch.setattr("app.wiki.git.commit_file", commit_file)
    monkeypatch.setattr("app.wiki.git.head_sha_for_path", lambda _p: _SHA_A)

    with pytest.raises(CommitMaxRetriesError):
        wiki_git.commit_with_retry(_PATH, new_body="body", message=_MSG, max_retries=2)

    assert commit_file.call_count == 3  # initial + 2 retries


# ---------------------------------------------------------------------------
# base_body provided — merge helpers
# ---------------------------------------------------------------------------


def _wire(monkeypatch, *, head_shas, worktrees, merge_result=None, commit_fn=None):
    head_iter = iter(head_shas)
    body_iter = iter(worktrees)
    monkeypatch.setattr("app.wiki.git.head_sha_for_path", lambda _p: next(head_iter))
    monkeypatch.setattr("app.wiki.git._read_worktree", lambda _p: next(body_iter))
    if merge_result is not None:
        monkeypatch.setattr("app.wiki.git.merge_content", lambda *_a: merge_result)
    commit_file = commit_fn or MagicMock(return_value=_SHA_B)
    monkeypatch.setattr("app.wiki.git.commit_file", commit_file)
    return commit_file


def test_base_no_concurrent_change(monkeypatch):
    merge_content = MagicMock()
    monkeypatch.setattr("app.wiki.git.merge_content", merge_content)
    commit_file = _wire(monkeypatch, head_shas=[_SHA_A, _SHA_A], worktrees=[_BASE])

    sha, body = wiki_git.commit_with_retry(
        _PATH, base_body=_BASE, new_body=_NEW, message=_MSG, max_retries=0
    )

    assert sha == _SHA_B
    assert body == _NEW
    merge_content.assert_not_called()
    commit_file.assert_called_once()


def test_base_clean_merge(monkeypatch):
    concurrent = "concurrent edit\n"
    merged = "merged\n"
    commit_file = _wire(
        monkeypatch,
        head_shas=[_SHA_A, _SHA_A],
        worktrees=[concurrent],
        merge_result=MergeResult(merged=merged, clean=True),
    )

    sha, body = wiki_git.commit_with_retry(
        _PATH, base_body=_BASE, new_body=_NEW, message=_MSG, max_retries=0
    )

    assert sha == _SHA_B
    assert body == merged
    commit_file.assert_called_once_with(_PATH, merged, _MSG, author=None)


def test_base_conflict_raises_without_resolver(monkeypatch):
    commit_file = _wire(
        monkeypatch,
        head_shas=[_SHA_A, _SHA_A],
        worktrees=["concurrent edit\n"],
        merge_result=MergeResult(merged="<<<<\n", clean=False),
    )

    with pytest.raises(GitMergeConflictError):
        wiki_git.commit_with_retry(
            _PATH, base_body=_BASE, new_body=_NEW, message=_MSG, max_retries=0
        )

    commit_file.assert_not_called()



def test_base_head_moves_then_commits(monkeypatch):
    concurrent_v1 = "concurrent v1\n"
    concurrent_v2 = "concurrent v2\n"
    merged_v2 = "merged v2\n"

    # Attempt 0: pre=A, post=B (HEAD moved) -> retry
    # Attempt 1: pre=B, post=B (stable) -> commit
    head_iter = iter([_SHA_A, _SHA_B, _SHA_B, _SHA_B])
    body_iter = iter([concurrent_v1, concurrent_v2])
    merge_iter = iter([
        MergeResult(merged="merged v1\n", clean=True),
        MergeResult(merged=merged_v2, clean=True),
    ])
    monkeypatch.setattr("app.wiki.git.head_sha_for_path", lambda _p: next(head_iter))
    monkeypatch.setattr("app.wiki.git._read_worktree", lambda _p: next(body_iter))
    monkeypatch.setattr("app.wiki.git.merge_content", lambda *_a: next(merge_iter))
    commit_file = MagicMock(return_value=_SHA_C)
    monkeypatch.setattr("app.wiki.git.commit_file", commit_file)

    sha, body = wiki_git.commit_with_retry(
        _PATH, base_body=_BASE, new_body=_NEW, message=_MSG, max_retries=1
    )

    assert sha == _SHA_C
    assert body == merged_v2
    assert commit_file.call_count == 1


def test_base_lock_race_then_commits(monkeypatch):
    concurrent = "concurrent edit\n"
    merged = "merged\n"

    head_iter = iter([_SHA_A, _SHA_A, _SHA_B, _SHA_B])
    body_iter = iter([_BASE, concurrent])
    merge_iter = iter([MergeResult(merged=merged, clean=True)])
    monkeypatch.setattr("app.wiki.git.head_sha_for_path", lambda _p: next(head_iter))
    monkeypatch.setattr("app.wiki.git._read_worktree", lambda _p: next(body_iter))
    monkeypatch.setattr("app.wiki.git.merge_content", lambda *_a: next(merge_iter))

    calls = 0

    def _commit(*_a, **_k):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise GitCommitLockError(_PATH)
        return _SHA_C

    monkeypatch.setattr("app.wiki.git.commit_file", _commit)

    sha, body = wiki_git.commit_with_retry(
        _PATH, base_body=_BASE, new_body=_NEW, message=_MSG, max_retries=1
    )

    assert sha == _SHA_C
    assert body == merged
    assert calls == 2


def test_base_raises_when_max_retries_exceeded(monkeypatch):
    head_iter = iter([_SHA_A, _SHA_A, _SHA_A, _SHA_A])
    body_iter = iter([_BASE, _BASE])
    monkeypatch.setattr("app.wiki.git.head_sha_for_path", lambda _p: next(head_iter))
    monkeypatch.setattr("app.wiki.git._read_worktree", lambda _p: next(body_iter))
    monkeypatch.setattr(
        "app.wiki.git.commit_file",
        MagicMock(side_effect=GitCommitLockError(_PATH)),
    )

    with pytest.raises(CommitMaxRetriesError) as exc_info:
        wiki_git.commit_with_retry(
            _PATH, base_body=_BASE, new_body=_NEW, message=_MSG, max_retries=1
        )

    assert exc_info.value.retries == 1
