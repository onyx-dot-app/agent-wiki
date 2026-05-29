"""Unit tests for the 3-way merge loop in ``commit_and_fan_out`` on the AI
write path (``ai_merge=True``) — an unresolvable git merge falls back to the
LLM merge rather than raising.

All external I/O (git, filesystem, fan-out, DB) is monkeypatched so the tests
run without a real repo or database.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.wiki.utils import commit_and_fan_out
from app.models.wiki import ChangeKind, CommitMaxRetriesError, CommitResult
from app.wiki.git import MergeResult

_PATH = "docs/page.md"
_MSG = "update page"
_BASE = "base content\n"
_NEW = "new content\n"
_SHA_A = "aaaa1111"
_SHA_B = "bbbb2222"
_SHA_C = "cccc3333"


@pytest.fixture(autouse=True)
def _stub_side_effects(monkeypatch):
    """Stub the commit leaf so no DB/git/notify runs.

    With no current user the activity block is skipped and ``author_string``
    resolves to the fallback author, so the loop can run in isolation.
    """
    monkeypatch.setattr("app.wiki.utils._current_user_or_none", lambda: None)
    monkeypatch.setattr("app.wiki.utils.wiki_notify.after_doc_write", MagicMock())


def _wire(monkeypatch, *, head_shas, current_bodies, merge_result=None, commit_sha=_SHA_B):
    """Wire the loop's leaf dependencies.

    ``head_shas`` — successive ``head_sha_for_path`` returns (each merge
    iteration calls it twice: before and after the merge).
    ``current_bodies`` — successive ``_read_head_or_empty`` returns.
    """
    head_iter = iter(head_shas)
    body_iter = iter(current_bodies)
    monkeypatch.setattr("app.wiki.utils.wiki_git.head_sha_for_path", lambda _p: next(head_iter))
    monkeypatch.setattr("app.wiki.utils._read_head_or_empty", lambda _p: next(body_iter))
    if merge_result is not None:
        monkeypatch.setattr("app.wiki.utils.wiki_git.merge_content", lambda *_a: merge_result)
    commit_file = MagicMock(return_value=commit_sha)
    monkeypatch.setattr("app.wiki.utils.wiki_git.commit_file", commit_file)
    return commit_file


# ---------------------------------------------------------------------------
# Happy path: no concurrent change
# ---------------------------------------------------------------------------


def test_no_concurrent_change_commits_new_body(monkeypatch):
    """When current == base, commits new_body directly."""
    merge_content = MagicMock()
    monkeypatch.setattr("app.wiki.utils.wiki_git.merge_content", merge_content)
    commit_file = _wire(
        monkeypatch,
        head_shas=[_SHA_A, _SHA_A],   # pre-merge, post-merge — same
        current_bodies=[_BASE],
        commit_sha=_SHA_B,
    )

    result = commit_and_fan_out(
        _PATH, _NEW, _MSG, change_kind=ChangeKind.EDIT,
        base_body=_BASE, ai_merge=True, max_retries=0, skip_acl=True,
    )

    assert isinstance(result, CommitResult)
    assert result.sha == _SHA_B
    assert result.old_body == _BASE
    assert result.new_body == _NEW
    merge_content.assert_not_called()
    commit_file.assert_called_once()


# ---------------------------------------------------------------------------
# No-op: merged == current
# ---------------------------------------------------------------------------


def test_noop_returns_none(monkeypatch):
    """When new_body equals current content, returns None without committing."""
    commit_file = _wire(
        monkeypatch,
        head_shas=[_SHA_A],   # only one call — exits before post-SHA check
        current_bodies=[_BASE],
    )

    result = commit_and_fan_out(
        _PATH, _BASE, _MSG, change_kind=ChangeKind.EDIT,
        base_body=_BASE, ai_merge=True, max_retries=0, skip_acl=True,
    )

    assert result is None
    commit_file.assert_not_called()


# ---------------------------------------------------------------------------
# Concurrent change — clean 3-way merge
# ---------------------------------------------------------------------------


def test_clean_3way_merge_commits_merged(monkeypatch):
    """When HEAD moved, a clean git merge yields the merged body."""
    concurrent_body = "concurrent edit\n"
    merged_body = "merged edit\n"

    commit_file = _wire(
        monkeypatch,
        head_shas=[_SHA_A, _SHA_A],   # HEAD stable by post-check
        current_bodies=[concurrent_body],
        merge_result=MergeResult(merged=merged_body, clean=True),
        commit_sha=_SHA_B,
    )

    result = commit_and_fan_out(
        _PATH, _NEW, _MSG, change_kind=ChangeKind.EDIT,
        base_body=_BASE, ai_merge=True, max_retries=0, skip_acl=True,
    )

    assert isinstance(result, CommitResult)
    assert result.new_body == merged_body
    assert result.old_body == concurrent_body
    commit_file.assert_called_once()


# ---------------------------------------------------------------------------
# Concurrent change — conflict, LLM fallback
# ---------------------------------------------------------------------------


def test_llm_fallback_on_conflict(monkeypatch):
    """When git merge has conflicts, the LLM merge resolver is used."""
    concurrent_body = "concurrent edit\n"
    llm_result = "llm merged\n"
    conflicted = "<<<<<<< BASE\n...\n"

    mcu = MagicMock()
    mcu.merge.return_value = llm_result
    monkeypatch.setattr("app.wiki.utils.merge_conflict_update", mcu)

    commit_file = _wire(
        monkeypatch,
        head_shas=[_SHA_A, _SHA_A],
        current_bodies=[concurrent_body],
        merge_result=MergeResult(merged=conflicted, clean=False),
        commit_sha=_SHA_B,
    )

    result = commit_and_fan_out(
        _PATH, _NEW, _MSG, change_kind=ChangeKind.EDIT,
        base_body=_BASE, ai_merge=True, max_retries=0, skip_acl=True,
    )

    assert isinstance(result, CommitResult)
    assert result.new_body == llm_result
    mcu.merge.assert_called_once_with(
        wiki_path=_PATH,
        base_body=_BASE,
        current_body=concurrent_body,
        draft_body=_NEW,
    )
    commit_file.assert_called_once()


# ---------------------------------------------------------------------------
# HEAD moves once (one retry), then stabilizes
# ---------------------------------------------------------------------------


def test_retries_when_head_moves_mid_merge(monkeypatch):
    """When HEAD moves between the pre/post SHA checks, the loop retries."""
    concurrent_body = "concurrent v1\n"
    concurrent_body_v2 = "concurrent v2\n"
    merged_v1 = "merged v1\n"
    merged_v2 = "merged v2\n"

    # Attempt 0: pre=A, post=B (HEAD moved) → retry
    # Attempt 1: pre=B, post=B (stable) → commit
    head_iter = iter([_SHA_A, _SHA_B, _SHA_B, _SHA_B])
    body_iter = iter([concurrent_body, concurrent_body_v2])
    merge_iter = iter([
        MergeResult(merged=merged_v1, clean=True),
        MergeResult(merged=merged_v2, clean=True),
    ])
    monkeypatch.setattr("app.wiki.utils.wiki_git.head_sha_for_path", lambda _p: next(head_iter))
    monkeypatch.setattr("app.wiki.utils._read_head_or_empty", lambda _p: next(body_iter))
    monkeypatch.setattr("app.wiki.utils.wiki_git.merge_content", lambda *_a: next(merge_iter))
    commit_file = MagicMock(return_value=_SHA_C)
    monkeypatch.setattr("app.wiki.utils.wiki_git.commit_file", commit_file)

    result = commit_and_fan_out(
        _PATH, _NEW, _MSG, change_kind=ChangeKind.EDIT,
        base_body=_BASE, ai_merge=True, max_retries=1, skip_acl=True,
    )

    assert isinstance(result, CommitResult)
    assert result.sha == _SHA_C
    assert commit_file.call_count == 1


# ---------------------------------------------------------------------------
# Max retries exceeded
# ---------------------------------------------------------------------------


def test_raises_when_max_retries_exceeded(monkeypatch):
    """Raises CommitMaxRetriesError when HEAD keeps moving."""
    concurrent_body = "concurrent\n"
    merged_body = "merged\n"

    # HEAD keeps returning a new SHA on every post-check
    head_shas = [_SHA_A, _SHA_B, _SHA_B, _SHA_C, _SHA_C, "dddd4444"]
    body_iter = iter([concurrent_body] * 10)
    head_iter = iter(head_shas)
    monkeypatch.setattr("app.wiki.utils.wiki_git.head_sha_for_path", lambda _p: next(head_iter))
    monkeypatch.setattr("app.wiki.utils._read_head_or_empty", lambda _p: next(body_iter))
    monkeypatch.setattr(
        "app.wiki.utils.wiki_git.merge_content",
        lambda *_a: MergeResult(merged=merged_body, clean=True),
    )
    commit_file = MagicMock(return_value=_SHA_A)
    monkeypatch.setattr("app.wiki.utils.wiki_git.commit_file", commit_file)

    with pytest.raises(CommitMaxRetriesError) as exc_info:
        commit_and_fan_out(
            _PATH, _NEW, _MSG, change_kind=ChangeKind.EDIT,
            base_body=_BASE, ai_merge=True, max_retries=2, skip_acl=True,
        )

    assert exc_info.value.retries == 2
    commit_file.assert_not_called()
