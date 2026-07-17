"""Executor for approved delete_empty_folder proposals.

Executes as a trash-move (soft delete) against a real tmp repo, re-validating
the folder is still empty first.
"""
from __future__ import annotations

import pytest

from app.wiki import git as wiki_git
from app.wiki.automanage import executor
from app.wiki.change_proposals import (
    ProposalCreatedVia,
    ProposalOp,
    approve,
    create,
    get,
)
from tests._seed import seed_user


@pytest.fixture
def repo(tmp_repo):
    return tmp_repo


def _proposal_for(folder: str) -> int:
    meta = wiki_git.last_commit_meta_for_path(folder)
    base = meta[0] if meta else "0" * 40
    return create(
        op=ProposalOp.DELETE_EMPTY_FOLDER,
        source_paths=[folder],
        target_paths=[],
        base_shas={folder: base},
        summary=f"Delete empty folder “{folder}”",
        created_via=ProposalCreatedVia.SWEEP,
    )["id"]


def test_execute_trashes_empty_folder(repo):
    uid = seed_user(uid="u1", email="u@x.com")
    wiki_git.commit_file("stale/.gitkeep", "", "create stale folder", author=None)
    pid = _proposal_for("stale")
    assert approve(pid, user_id=uid)  # pending -> approved

    executor.execute(pid)

    # The folder is gone from the tracked tree (moved into .trash/).
    assert "stale/.gitkeep" not in wiki_git.list_paths()
    p = get(pid)
    assert p is not None
    assert p["status"] == "applied"
    assert p["applied_sha"]


def test_execute_skips_folder_that_is_no_longer_empty(repo):
    uid = seed_user(uid="u1", email="u@x.com")
    wiki_git.commit_file("live/.gitkeep", "", "keep", author=None)
    pid = _proposal_for("live")
    assert approve(pid, user_id=uid)
    # A page lands in the folder after approval — it must not be deleted.
    wiki_git.commit_file("live/page.md", "# Page\n", "add a page", author=None)

    executor.execute(pid)

    p = get(pid)
    assert p is not None
    assert p["status"] == "stale"
    assert "live/page.md" in wiki_git.list_paths()  # untouched


def test_execute_is_noop_when_not_approved(repo):
    wiki_git.commit_file("x/.gitkeep", "", "create", author=None)
    pid = _proposal_for("x")  # still pending — never approved

    executor.execute(pid)

    p = get(pid)
    assert p is not None
    assert p["status"] == "pending"  # unchanged
    assert "x/.gitkeep" in wiki_git.list_paths()  # untouched
