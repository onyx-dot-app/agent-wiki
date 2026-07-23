"""Execution-time premise re-validation — the ``Detector.validate`` seam.

A proposal records which detector authored it; before applying, the executor
routes a premise re-check back to that detector. Premise-based, not sha-based:
what invalidates a proposal is its *claim* no longer holding (folder no longer
empty), not any commit having touched an affected path.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.tasks.queues import automanage_nearline_queue
from app.wiki import git as wiki_git
from app.wiki.automanage import executor, review, runner
from app.wiki.automanage.detectors import DETECTORS_BY_NAME
from app.wiki.automanage.detectors.empty_folder import _EmptyFolderDetector
from app.wiki.change_proposals import (
    ProposalCreatedVia,
    ProposalOp,
    ProposalStatus,
    create,
    get as get_proposal,
    list_by_status,
)
from tests._seed import seed_user


@pytest.fixture
def repo(tmp_repo, tmp_config):
    wiki_git.commit_file("keep/page.md", "# Keep\n", "seed", author=None)
    wiki_git.commit_file("hollow/.gitkeep", "", "empty folder", author=None)
    return tmp_config


@pytest.fixture(autouse=True)
def _eager_detector(monkeypatch):
    monkeypatch.setattr(runner, "DETECTORS", [_EmptyFolderDetector(min_age_days=0)])


def _pending_one():
    pending = list_by_status(ProposalStatus.PENDING)
    assert len(pending) == 1
    return pending[0]


def test_emitted_proposal_records_its_detector(repo):
    runner.run_sweep(triggered_by_user_id=None)
    assert _pending_one()["detector"] == "empty_folder"


def test_validate_holds_while_premise_true(repo):
    runner.run_sweep(triggered_by_user_id=None)
    p = _pending_one()
    assert DETECTORS_BY_NAME["empty_folder"].validate(p) is None


def test_validate_stales_when_premise_breaks(repo):
    runner.run_sweep(triggered_by_user_id=None)
    p = _pending_one()
    wiki_git.commit_file("hollow/late.md", "# Late\n", "fill folder", author=None)

    reason = DETECTORS_BY_NAME["empty_folder"].validate(p)
    assert reason is not None and "no longer an empty folder" in reason


def test_executor_dispatches_validation_to_the_authoring_detector(repo, monkeypatch):
    """The generic gate stales an approved proposal whose premise broke —
    dispatched by the detector name stamped on the row. Proven with a stub
    whose validate always fails, so the outcome is attributable to the gate
    (not an op-specific inline check)."""
    runner.run_sweep(triggered_by_user_id=None)
    p = _pending_one()

    class _AlwaysInvalid:
        name = "empty_folder"

        def applicable(self, trigger: Any) -> bool:
            return True

        def detect(self, scope: Any) -> list[Any]:
            return []

        def validate(self, proposal: dict[str, Any]) -> str | None:
            return "stub says the premise broke"

    monkeypatch.setitem(
        executor.DETECTORS_BY_NAME, "empty_folder", _AlwaysInvalid()
    )
    uid = seed_user(uid="rv", email="rv@x.com")
    with automanage_nearline_queue.immediate_mode():
        review.approve(p["id"], user_id=uid)

    refreshed = get_proposal(p["id"])
    assert refreshed is not None
    assert refreshed["status"] == "stale"
    assert refreshed["status_reason"] == "stub says the premise broke"
    assert "hollow/.gitkeep" in wiki_git.list_paths()  # nothing executed


def test_unstamped_proposal_skips_the_gate_and_executes(repo):
    """Rows without a detector (predate the column / created outside the
    pipeline) skip premise validation; op-specific checks still apply."""
    pid = create(
        op=ProposalOp.DELETE_EMPTY_FOLDER,
        source_paths=["hollow"],
        target_paths=[],
        base_shas={"hollow": "0" * 40},
        summary="Delete empty folder “hollow”",
        created_via=ProposalCreatedVia.SWEEP,
    )["id"]
    uid = seed_user(uid="rv", email="rv@x.com")
    with automanage_nearline_queue.immediate_mode():
        review.approve(pid, user_id=uid)

    p = get_proposal(pid)
    assert p is not None
    assert p["status"] == "applied"  # executed normally without the gate


def test_unknown_detector_fails_closed(repo, monkeypatch):
    """A *stamped* proposal whose detector no longer exists (renamed/removed,
    mixed-version worker) must not execute — the premise can't be re-checked."""
    runner.run_sweep(triggered_by_user_id=None)
    p = _pending_one()
    monkeypatch.delitem(executor.DETECTORS_BY_NAME, "empty_folder")

    uid = seed_user(uid="rv", email="rv@x.com")
    with automanage_nearline_queue.immediate_mode():
        review.approve(p["id"], user_id=uid)

    refreshed = get_proposal(p["id"])
    assert refreshed is not None
    assert refreshed["status"] == "stale"
    assert "unknown" in (refreshed["status_reason"] or "")
    assert "hollow/.gitkeep" in wiki_git.list_paths()  # nothing executed
