"""Unit tests for the raise-on-conflict path of ``commit_and_fan_out``.

This is the human-edit path (``PUT /file``): a read-modify-write with
``ai_merge`` off, so an unresolvable 3-way merge raises
``GitMergeConflictError`` (the caller turns it into a 409) and HEAD that keeps
moving raises ``CommitMaxRetriesError``. Ref-lock races are handled
transparently inside ``commit_file``.

All git/filesystem/DB I/O is monkeypatched so the tests run without a real
repo or database.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.wiki.utils import commit_and_fan_out
from app.wiki.git import GitMergeConflictError, MergeResult
from app.models.wiki import ChangeKind, CommitMaxRetriesError, CommitResult

_PATH = "docs/page.md"
_MSG = "update page"
_BASE = "base content\n"
_NEW = "new content\n"
_SHA_A = "aaaa1111"
_SHA_B = "bbbb2222"
_SHA_C = "cccc3333"


@pytest.fixture(autouse=True)
def _stub_side_effects(monkeypatch):
    """Stub the commit leaf so no DB/git/notify runs; no current user means
    the activity block is skipped and ``author_string`` uses the fallback."""
    monkeypatch.setattr("app.wiki.utils._current_user_or_none", lambda: None)
    monkeypatch.setattr("app.wiki.utils.wiki_notify.after_doc_write", MagicMock())


def _commit(path=_PATH, body=_NEW, **kwargs):
    return commit_and_fan_out(
        path, body, _MSG, change_kind=ChangeKind.EDIT, skip_acl=True, **kwargs
    )


# ---------------------------------------------------------------------------
# base_body=None — no merge, direct commit
# ---------------------------------------------------------------------------


def test_no_base_passes_through(monkeypatch):
    commit_file = MagicMock(return_value=_SHA_A)
    monkeypatch.setattr("app.wiki.utils.wiki_git.commit_file", commit_file)
    monkeypatch.setattr("app.wiki.utils._read_head_or_empty", lambda _p: "")
    monkeypatch.setattr(
        "app.wiki.utils.wiki_git.merge_content",
        MagicMock(side_effect=AssertionError("no merge without a base")),
    )

    result = _commit(body="body")

    assert isinstance(result, CommitResult)
    assert result.sha == _SHA_A
    assert result.new_body == "body"
    commit_file.assert_called_once()


def _wire(monkeypatch, *, head_shas, current_bodies, merge_result=None, commit_sha=_SHA_B):
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
# base_body provided — merge semantics
# ---------------------------------------------------------------------------


def test_base_no_concurrent_change(monkeypatch):
    merge_content = MagicMock()
    monkeypatch.setattr("app.wiki.utils.wiki_git.merge_content", merge_content)
    commit_file = _wire(monkeypatch, head_shas=[_SHA_A, _SHA_A], current_bodies=[_BASE])

    result = _commit(base_body=_BASE, max_retries=0)

    assert isinstance(result, CommitResult)
    assert result.sha == _SHA_B
    assert result.new_body == _NEW
    merge_content.assert_not_called()
    commit_file.assert_called_once()


def test_base_clean_merge(monkeypatch):
    concurrent = "concurrent edit\n"
    merged = "merged\n"
    commit_file = _wire(
        monkeypatch,
        head_shas=[_SHA_A, _SHA_A],
        current_bodies=[concurrent],
        merge_result=MergeResult(merged=merged, clean=True),
    )

    result = _commit(base_body=_BASE, max_retries=0)

    assert isinstance(result, CommitResult)
    assert result.sha == _SHA_B
    assert result.new_body == merged
    commit_file.assert_called_once()


def test_base_conflict_raises_without_resolver(monkeypatch):
    commit_file = _wire(
        monkeypatch,
        head_shas=[_SHA_A, _SHA_A],
        current_bodies=["concurrent edit\n"],
        merge_result=MergeResult(merged="<<<<\n", clean=False),
    )

    with pytest.raises(GitMergeConflictError):
        _commit(base_body=_BASE, max_retries=0)

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
    monkeypatch.setattr("app.wiki.utils.wiki_git.head_sha_for_path", lambda _p: next(head_iter))
    monkeypatch.setattr("app.wiki.utils._read_head_or_empty", lambda _p: next(body_iter))
    monkeypatch.setattr("app.wiki.utils.wiki_git.merge_content", lambda *_a: next(merge_iter))
    commit_file = MagicMock(return_value=_SHA_C)
    monkeypatch.setattr("app.wiki.utils.wiki_git.commit_file", commit_file)

    result = _commit(base_body=_BASE, max_retries=1)

    assert isinstance(result, CommitResult)
    assert result.sha == _SHA_C
    assert result.new_body == merged_v2
    assert commit_file.call_count == 1


def test_base_raises_when_max_retries_exceeded(monkeypatch):
    """CommitMaxRetriesError when HEAD keeps moving during the merge step."""
    # HEAD always returns a different SHA on the post-check — commit is never attempted.
    head_shas = [_SHA_A, _SHA_B, _SHA_B, _SHA_C, _SHA_C, "dddd4444"]
    body_iter = iter(["concurrent\n"] * 10)
    head_iter = iter(head_shas)
    monkeypatch.setattr("app.wiki.utils.wiki_git.head_sha_for_path", lambda _p: next(head_iter))
    monkeypatch.setattr("app.wiki.utils._read_head_or_empty", lambda _p: next(body_iter))
    monkeypatch.setattr(
        "app.wiki.utils.wiki_git.merge_content",
        lambda *_a: MergeResult(merged="merged\n", clean=True),
    )
    commit_file = MagicMock(return_value=_SHA_A)
    monkeypatch.setattr("app.wiki.utils.wiki_git.commit_file", commit_file)

    with pytest.raises(CommitMaxRetriesError) as exc_info:
        _commit(base_body=_BASE, max_retries=2)

    assert exc_info.value.retries == 2
    commit_file.assert_not_called()
