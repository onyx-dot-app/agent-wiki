"""Detection runner — the substrate that turns detectors into persisted
proposals + a run record.

Uses a zero-age empty-folder detector (via monkeypatched ``DETECTORS``) so the
fresh tmp-repo commits qualify without forging commit dates; the age gate
itself is covered in ``test_empty_folder_detector.py``.
"""
from __future__ import annotations

import pytest

from app.auth.users import AI_USER_ID
from app.tasks.queues import automanage_offline_queue
from app.wiki import git as wiki_git
from app.wiki import update_policy
from app.wiki.automanage import runner, runs
from app.wiki.automanage.detectors.empty_folder import _EmptyFolderDetector
from app.wiki.change_proposals import ProposalStatus, list_by_status, reject
from tests._seed import seed_user


@pytest.fixture
def repo(tmp_repo, tmp_config):
    from app.wiki import git as wiki_git

    wiki_git.commit_file("team/plan.md", "# Plan\n", "seed", author=None)
    wiki_git.commit_file("archive/.gitkeep", "", "create archive", author=None)
    wiki_git.commit_file("old/notes.md", "# Notes\n", "seed", author=None)
    wiki_git.commit_file("old/.gitkeep", "", "keep old", author=None)
    wiki_git.delete_path("old/notes.md", "remove notes", author=None)
    return tmp_config


@pytest.fixture(autouse=True)
def _eager_detector(monkeypatch):
    monkeypatch.setattr(runner, "DETECTORS", [_EmptyFolderDetector(min_age_days=0)])


def test_sweep_emits_proposals_and_records_run(repo):
    result = runner.run_sweep(triggered_by_user_id=None)

    assert result["proposals_emitted"] == 2
    pending = list_by_status(ProposalStatus.PENDING)
    assert {p["source_paths"][0] for p in pending} == {"archive", "old"}
    for p in pending:
        assert p["op"] == "delete_empty_folder"
        assert p["target_paths"] == []
        assert p["run_id"] == result["run_id"]
        assert p["created_via"] == "sweep"
        # Drift anchor attached by the runner.
        assert p["base_shas"].get(p["source_paths"][0])

    run = runs.get(result["run_id"])
    assert run is not None
    assert run["status"] == "completed"
    assert run["trigger"] == "sweep"
    assert run["proposals_emitted"] == 2
    assert run["paths_scanned"] >= 3
    assert run["finished_at"] is not None


def test_dedupe_blocks_repropose_of_pending(repo):
    first = runner.run_sweep(triggered_by_user_id=None)
    assert first["proposals_emitted"] == 2
    # Pending proposals from the first run block an identical second emit.
    second = runner.run_sweep(triggered_by_user_id=None)
    assert second["proposals_emitted"] == 0
    assert len(list_by_status(ProposalStatus.PENDING)) == 2


def test_rejected_is_not_reproposed(repo):
    uid = seed_user(uid="admin_1", email="a@x.com", is_admin=True)
    runner.run_sweep(triggered_by_user_id=None)
    for p in list_by_status(ProposalStatus.PENDING):
        assert reject(p["id"], user_id=uid, reason="not wanted")
    # No pending remain, but the rejected path-sets stay on the do-not-propose
    # list, so a fresh sweep emits nothing.
    again = runner.run_sweep(triggered_by_user_id=None)
    assert again["proposals_emitted"] == 0
    assert list_by_status(ProposalStatus.PENDING) == []


def test_forbidden_scope_is_skipped(repo):
    update_policy.set_policy("archive", ai_management_allowed=False)
    result = runner.run_sweep(triggered_by_user_id=None)
    folders = {p["source_paths"][0] for p in list_by_status(ProposalStatus.PENDING)}
    assert "archive" not in folders  # explicitly do-not-manage
    assert "old" in folders
    assert result["proposals_emitted"] == 1


def test_ai_managed_scope_auto_applies(repo):
    # archive opts into AI management → auto-approved + executed as the AI user,
    # no human queue. old is unset → stays pending.
    update_policy.set_policy("archive", ai_management_allowed=True)
    with automanage_offline_queue.immediate_mode():
        runner.run_sweep(triggered_by_user_id=None)

    applied = {p["source_paths"][0]: p for p in list_by_status(ProposalStatus.APPLIED)}
    assert "archive" in applied
    assert applied["archive"]["reviewed_by_user_id"] is None  # no human reviewer
    assert applied["archive"]["acting_user_id"] == AI_USER_ID
    assert "archive/.gitkeep" not in wiki_git.list_paths()  # trashed

    pending = {p["source_paths"][0] for p in list_by_status(ProposalStatus.PENDING)}
    assert pending == {"old"}  # unset scope waits for a human
    assert "old/.gitkeep" in wiki_git.list_paths()  # untouched


def test_unset_scope_stays_pending(repo):
    # No policy anywhere → both empties wait for human review, nothing executes.
    with automanage_offline_queue.immediate_mode():
        runner.run_sweep(triggered_by_user_id=None)
    assert list_by_status(ProposalStatus.APPLIED) == []
    pending = {p["source_paths"][0] for p in list_by_status(ProposalStatus.PENDING)}
    assert pending == {"archive", "old"}
