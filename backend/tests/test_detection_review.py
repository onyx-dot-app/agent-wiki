"""Proposal review coordination — the shared approval→execution seam.

Both approval sources (human `approve`, AI-managed `auto_approve`) run the same
execution path; `reject` never executes. `immediate_mode` runs the enqueued
execution inline.
"""
from __future__ import annotations

import pytest

from app.auth.users import AI_USER_ID
from app.tasks.queues import automanage_nearline_queue, automanage_offline_queue
from app.wiki import git as wiki_git
from app.wiki.automanage import review
from app.wiki.change_proposals import (
    ProposalCreatedVia,
    ProposalOp,
    create as create_proposal,
    get as get_proposal,
)
from tests._seed import seed_user


@pytest.fixture
def repo(tmp_repo):
    return tmp_repo


def _mk(folder: str) -> int:
    return create_proposal(
        op=ProposalOp.DELETE_EMPTY_FOLDER,
        source_paths=[folder],
        target_paths=[],
        base_shas={folder: "0" * 40},
        summary=f"Delete empty folder “{folder}”",
        created_via=ProposalCreatedVia.SWEEP,
    )["id"]


def _get(pid: int) -> dict:
    p = get_proposal(pid)
    assert p is not None
    return p


def test_human_approve_executes_and_binds_reviewer(repo):
    uid = seed_user(uid="u1", email="u@x.com")
    wiki_git.commit_file("stale/.gitkeep", "", "create", author=None)
    pid = _mk("stale")
    with automanage_nearline_queue.immediate_mode():
        assert review.approve(pid, user_id=uid)
    p = _get(pid)
    assert p["status"] == "applied"
    assert p["reviewed_by_user_id"] == uid  # human reviewer bound
    assert "stale/.gitkeep" not in wiki_git.list_paths()


def test_auto_approve_executes_without_a_reviewer(repo):
    # The AI system user is seeded by migration; use it as the acting principal.
    wiki_git.commit_file("ai-managed/.gitkeep", "", "create", author=None)
    pid = _mk("ai-managed")
    with automanage_offline_queue.immediate_mode():
        assert review.auto_approve(pid, acting_user_id=AI_USER_ID)
    p = _get(pid)
    assert p["status"] == "applied"  # same execution path as human approval
    assert p["reviewed_by_user_id"] is None  # auto: no human reviewer
    assert p["acting_user_id"] == AI_USER_ID
    assert "ai-managed/.gitkeep" not in wiki_git.list_paths()


def test_reject_does_not_execute(repo):
    uid = seed_user(uid="u1", email="u@x.com")
    wiki_git.commit_file("junk/.gitkeep", "", "create", author=None)
    pid = _mk("junk")
    assert review.reject(pid, user_id=uid, reason="not wanted")
    p = _get(pid)
    assert p["status"] == "rejected"
    assert "junk/.gitkeep" in wiki_git.list_paths()  # untouched


def test_approve_non_pending_returns_false(repo):
    uid = seed_user(uid="u1", email="u@x.com")
    wiki_git.commit_file("x/.gitkeep", "", "create", author=None)
    pid = _mk("x")
    with automanage_nearline_queue.immediate_mode():
        assert review.approve(pid, user_id=uid)
        assert review.approve(pid, user_id=uid) is False  # already applied


def test_approve_recovers_stuck_approved(repo):
    # Simulate a prior approval whose enqueue failed: the DB transition to
    # 'approved' committed, but execution never ran. A retried approve must
    # re-dispatch (heal) rather than dead-end.
    from app.wiki.change_proposals import approve as cp_approve

    uid = seed_user(uid="u1", email="u@x.com")
    wiki_git.commit_file("stuck/.gitkeep", "", "create", author=None)
    pid = _mk("stuck")
    assert cp_approve(pid, user_id=uid)  # pending -> approved, no dispatch
    assert _get(pid)["status"] == "approved"

    with automanage_nearline_queue.immediate_mode():
        assert review.approve(pid, user_id=uid) is True  # re-dispatches
    assert _get(pid)["status"] == "applied"


def _mk_unsupported(path: str) -> int:
    """A proposal whose op is outside the policy allowlist (split is a valid
    ledger op with no producer)."""
    return create_proposal(
        op=ProposalOp.SPLIT,
        source_paths=[path],
        target_paths=["kept.md"],
        base_shas={path: "0" * 40},
        summary=f"Split “{path}” into “kept.md”",
        created_via=ProposalCreatedVia.SWEEP,
    )["id"]


def test_approve_refuses_op_without_executor(repo):
    """Emit safety: an op the executor can't apply must dead-end at approval —
    approving it would crash the execute task (or silently freeze)."""
    uid = seed_user(uid="u1", email="u@x.com")
    wiki_git.commit_file("dup.md", "# Dup\n", "create", author=None)
    pid = _mk_unsupported("dup.md")

    with automanage_nearline_queue.immediate_mode():
        assert review.approve(pid, user_id=uid) is False

    assert _get(pid)["status"] == "pending"  # untouched, no execution attempted


def test_auto_approve_refuses_op_without_executor(repo):
    wiki_git.commit_file("dup2.md", "# Dup\n", "create", author=None)
    pid = _mk_unsupported("dup2.md")

    with automanage_offline_queue.immediate_mode():
        assert review.auto_approve(pid, acting_user_id=AI_USER_ID) is False

    assert _get(pid)["status"] == "pending"
