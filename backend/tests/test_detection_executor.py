"""Executor for approved delete_empty_folder proposals.

Executes as a trash-move (soft delete) against a real tmp repo, re-validating
the folder is still empty first.
"""
from __future__ import annotations

import pytest

from app.auth.users import AI_USER_ID
from app.wiki import acl
from app.wiki import git as wiki_git
from app.wiki.automanage import executor
from app.wiki.change_proposals import (
    ProposalCreatedVia,
    ProposalOp,
    approve,
    auto_approve,
    create,
    get,
)
from tests._seed import list_events, seed_user


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


def test_auto_applied_delete_records_activity_event(repo):
    """An AI auto-applied cleanup (no human reviewer) emits an
    ``automanage.applied`` event so admins/owners get an audit trail."""
    # The AI system user (AI_USER_ID) is seeded by migration, so no seed here.
    wiki_git.commit_file("area/gone/.gitkeep", "", "create gone folder", author=None)
    pid = _proposal_for("area/gone")
    assert auto_approve(pid, acting_user_id=AI_USER_ID)  # pending -> approved, no reviewer

    executor.execute(pid)

    assert get(pid)["status"] == "applied"  # type: ignore[index]
    events = list_events(kind=executor.EVENT_AUTOMANAGE_APPLIED)
    assert len(events) == 1
    ev = events[0]
    assert ev["actor"] == AI_USER_ID
    # Targets the folder itself: trashing a folder doesn't re-point its owner
    # row (unlike a page), so the folder's owner still resolves here.
    assert ev["target"] == "area/gone"
    assert ev["payload"]["op"] == ProposalOp.DELETE_EMPTY_FOLDER.value
    assert ev["payload"]["source_paths"] == ["area/gone"]
    assert ev["payload"]["applied_sha"]


def test_trashed_folder_owner_still_resolves_at_original_path(repo):
    """Pins the behavior the auto-apply event target depends on: trashing a
    folder does NOT re-point its ``wiki_owners`` row (only a page's row moves),
    so the folder's owner still resolves at the original path and can see the
    ``automanage.applied`` event keyed there. If this ever changes, the event
    visibility silently regresses — so guard it here."""
    uid = seed_user(uid="owner1", email="owner@x.com")
    wiki_git.commit_file("keep/gone/.gitkeep", "", "create", author=None)
    acl.set_owner("keep/gone", uid)
    pid = _proposal_for("keep/gone")
    assert auto_approve(pid, acting_user_id=AI_USER_ID)

    executor.execute(pid)

    assert "keep/gone/.gitkeep" not in wiki_git.list_paths()  # trashed
    assert acl.get_owner("keep/gone") == uid  # owner row stayed → event reaches them


def test_human_approved_delete_records_no_event(repo):
    """A human-approved apply is already visible to the approver (they watched
    the review banner), so it must NOT emit the auto-applied audit event."""
    uid = seed_user(uid="u1", email="u@x.com")
    wiki_git.commit_file("byhand/.gitkeep", "", "create", author=None)
    pid = _proposal_for("byhand")
    assert approve(pid, user_id=uid)  # human reviewer set

    executor.execute(pid)

    assert get(pid)["status"] == "applied"  # type: ignore[index]
    assert list_events(kind=executor.EVENT_AUTOMANAGE_APPLIED) == []


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
