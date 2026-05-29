"""Unit tests for ``commit_with_ai_rebase`` in ``wiki_utils``.

All external I/O (git, filesystem, fan-out) is monkeypatched so the tests
run without a real repo or database.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.wiki.utils import commit_with_ai_rebase
from app.models.wiki import AiRebaseMaxRetriesError, CommitResult
from app.wiki.git import MergeResult

_PATH = "docs/page.md"
_MSG = "update page"
_BASE = "base content\n"
_NEW = "new content\n"
_SHA_A = "aaaa1111"
_SHA_B = "bbbb2222"
_SHA_C = "cccc3333"


@pytest.fixture(autouse=True)
def _stub_fan_out(monkeypatch):
    """Stub commit_and_fan_out to avoid any DB or git interaction."""
    stub = MagicMock(return_value=_SHA_A)
    monkeypatch.setattr("app.wiki.utils.commit_and_fan_out", stub)
    return stub


def _patch(monkeypatch, *, head_shas, current_bodies, merge_result=None, llm_merged=None):
    """Wire up the common monkeypatches.

    ``head_shas`` — list of return values for successive ``head_sha_for_path``
    calls (each loop iteration calls it twice: before and after the merge).
    ``current_bodies`` — list of return values for ``_read_head_or_empty``.
    """
    head_iter = iter(head_shas)
    body_iter = iter(current_bodies)

    wiki_git_mock = MagicMock()
    wiki_git_mock.head_sha_for_path.side_effect = lambda _p: next(head_iter)
    if merge_result is not None:
        wiki_git_mock.merge_content.return_value = merge_result
    monkeypatch.setattr("app.wiki.utils.wiki_git", wiki_git_mock)

    monkeypatch.setattr(
        "app.wiki.utils._read_head_or_empty",
        lambda _p: next(body_iter),
    )

    if llm_merged is not None:
        mcu = MagicMock()
        mcu.merge.return_value = llm_merged
        monkeypatch.setattr("app.wiki.utils.merge_conflict_update", mcu)

    return wiki_git_mock


# ---------------------------------------------------------------------------
# Happy path: no concurrent change
# ---------------------------------------------------------------------------


def test_no_concurrent_change_commits_new_body(monkeypatch):
    """When current == base, commits new_body directly."""
    wiki_git_mock = _patch(
        monkeypatch,
        head_shas=[_SHA_A, _SHA_A],   # pre-merge, post-merge — same
        current_bodies=[_BASE],
    )
    fan_out = MagicMock(return_value=_SHA_B)
    monkeypatch.setattr("app.wiki.utils.commit_and_fan_out", fan_out)

    result = commit_with_ai_rebase(
        _PATH, _MSG, base_body=_BASE, new_body=_NEW, max_retries=0
    )

    assert isinstance(result, CommitResult)
    assert result.sha == _SHA_B
    assert result.old_body == _BASE
    assert result.new_body == _NEW
    wiki_git_mock.merge_content.assert_not_called()
    fan_out.assert_called_once()


# ---------------------------------------------------------------------------
# No-op: merged == current
# ---------------------------------------------------------------------------


def test_noop_returns_none(monkeypatch):
    """When new_body equals current content, returns None without committing."""
    fan_out = MagicMock(return_value=_SHA_B)
    monkeypatch.setattr("app.wiki.utils.commit_and_fan_out", fan_out)
    _patch(
        monkeypatch,
        head_shas=[_SHA_A],   # only one call — exits before post-SHA check
        current_bodies=[_BASE],
    )

    result = commit_with_ai_rebase(
        _PATH, _MSG, base_body=_BASE, new_body=_BASE, max_retries=0
    )

    assert result is None
    fan_out.assert_not_called()


# ---------------------------------------------------------------------------
# Concurrent change — clean 3-way merge
# ---------------------------------------------------------------------------


def test_clean_3way_merge_commits_merged(monkeypatch):
    """When HEAD moved, a clean git merge yields the merged body."""
    concurrent_body = "concurrent edit\n"
    merged_body = "merged edit\n"

    wiki_git_mock = _patch(
        monkeypatch,
        head_shas=[_SHA_A, _SHA_A],   # HEAD stable by post-check
        current_bodies=[concurrent_body],
        merge_result=MergeResult(merged=merged_body, clean=True),
    )
    fan_out = MagicMock(return_value=_SHA_B)
    monkeypatch.setattr("app.wiki.utils.commit_and_fan_out", fan_out)

    result = commit_with_ai_rebase(
        _PATH, _MSG, base_body=_BASE, new_body=_NEW, max_retries=0
    )

    assert isinstance(result, CommitResult)
    assert result.new_body == merged_body
    assert result.old_body == concurrent_body
    wiki_git_mock.merge_content.assert_called_once_with(_BASE, concurrent_body, _NEW)


# ---------------------------------------------------------------------------
# Concurrent change — conflict, LLM fallback
# ---------------------------------------------------------------------------


def test_llm_fallback_on_conflict(monkeypatch):
    """When git merge has conflicts, the LLM merge is used."""
    concurrent_body = "concurrent edit\n"
    llm_result = "llm merged\n"
    conflicted = "<<<<<<< BASE\n...\n"

    mcu = MagicMock()
    mcu.merge.return_value = llm_result

    _patch(
        monkeypatch,
        head_shas=[_SHA_A, _SHA_A],
        current_bodies=[concurrent_body],
        merge_result=MergeResult(merged=conflicted, clean=False),
        llm_merged=llm_result,
    )
    monkeypatch.setattr("app.wiki.utils.merge_conflict_update", mcu)
    fan_out = MagicMock(return_value=_SHA_B)
    monkeypatch.setattr("app.wiki.utils.commit_and_fan_out", fan_out)

    result = commit_with_ai_rebase(
        _PATH, _MSG, base_body=_BASE, new_body=_NEW, max_retries=0
    )

    assert isinstance(result, CommitResult)
    assert result.new_body == llm_result
    mcu.merge.assert_called_once_with(
        wiki_path=_PATH,
        base_body=_BASE,
        current_body=concurrent_body,
        draft_body=_NEW,
    )


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
    head_shas = [_SHA_A, _SHA_B, _SHA_B, _SHA_B]
    current_bodies = [concurrent_body, concurrent_body_v2]
    merge_results = [
        MergeResult(merged=merged_v1, clean=True),
        MergeResult(merged=merged_v2, clean=True),
    ]

    merge_iter = iter(merge_results)
    body_iter = iter(current_bodies)
    head_iter = iter(head_shas)

    wiki_git_mock = MagicMock()
    wiki_git_mock.head_sha_for_path.side_effect = lambda _p: next(head_iter)
    wiki_git_mock.merge_content.side_effect = lambda *_: next(merge_iter)
    monkeypatch.setattr("app.wiki.utils.wiki_git", wiki_git_mock)
    monkeypatch.setattr(
        "app.wiki.utils._read_head_or_empty",
        lambda _p: next(body_iter),
    )

    fan_out = MagicMock(return_value=_SHA_C)
    monkeypatch.setattr("app.wiki.utils.commit_and_fan_out", fan_out)

    result = commit_with_ai_rebase(
        _PATH, _MSG, base_body=_BASE, new_body=_NEW, max_retries=1
    )

    assert isinstance(result, CommitResult)
    assert result.sha == _SHA_C
    assert wiki_git_mock.merge_content.call_count == 2


# ---------------------------------------------------------------------------
# Max retries exceeded
# ---------------------------------------------------------------------------


def test_raises_when_max_retries_exceeded(monkeypatch):
    """Raises AiRebaseMaxRetriesError when HEAD keeps moving."""
    concurrent_body = "concurrent\n"
    merged_body = "merged\n"

    # HEAD keeps returning a new SHA on every post-check
    head_shas = [_SHA_A, _SHA_B, _SHA_B, _SHA_C, _SHA_C, "dddd4444"]
    body_iter = iter([concurrent_body] * 10)
    head_iter = iter(head_shas)

    wiki_git_mock = MagicMock()
    wiki_git_mock.head_sha_for_path.side_effect = lambda _p: next(head_iter)
    wiki_git_mock.merge_content.return_value = MergeResult(merged=merged_body, clean=True)
    monkeypatch.setattr("app.wiki.utils.wiki_git", wiki_git_mock)
    monkeypatch.setattr(
        "app.wiki.utils._read_head_or_empty",
        lambda _p: next(body_iter),
    )
    fan_out = MagicMock(return_value=_SHA_A)
    monkeypatch.setattr("app.wiki.utils.commit_and_fan_out", fan_out)

    with pytest.raises(AiRebaseMaxRetriesError) as exc_info:
        commit_with_ai_rebase(
            _PATH, _MSG, base_body=_BASE, new_body=_NEW, max_retries=2
        )

    exc = exc_info.value
    assert exc.retries == 2
    fan_out.assert_not_called()
