"""Layer-1 dedup — finding identity, revival, rejection durability, cooldown.

Unit level: `Deduper.decide` against seeded proposal rows. Integration
level: two sweeps through the real runner — the second carries instead of
duplicating, and a staled row revives with its id (and history) intact.
"""
from __future__ import annotations

import pytest

from app.models.wiki import PathMove
from app.wiki import doc_ids
from app.wiki import git as wiki_git
from app.wiki.automanage import dedup, runner
from app.wiki.automanage.dedup import DedupAction, Deduper
from app.wiki.automanage.detectors.base import ProposalDraft
from app.wiki.automanage.detectors.empty_folder import _EmptyFolderDetector
from app.wiki.change_proposals import (
    ProposalCreatedVia,
    ProposalOp,
    create,
    get,
    mark_stale,
    reject,
)
from tests._seed import seed_user


@pytest.fixture
def repo(tmp_repo, tmp_db):
    wiki_git.commit_file("docs/a.md", "# A\n\nsame body\n", "seed", author=None)
    wiki_git.commit_file("docs/b.md", "# B\n\nsame body\n", "seed", author=None)
    return tmp_repo


def _draft(premise: str | None = "blob1") -> ProposalDraft:
    return ProposalDraft(
        op=ProposalOp.MERGE,
        source_paths=["docs/b.md"],
        target_paths=["docs/a.md"],
        summary="merge b into a",
        premise=premise,
    )


def _deduper(**kw) -> Deduper:
    return Deduper(wiki_git.list_paths(), **kw)


def _persist(decision: dedup.DedupDecision, draft: ProposalDraft) -> int:
    return create(
        op=draft.op,
        source_paths=draft.source_paths,
        target_paths=draft.target_paths,
        base_shas={p: "0" * 40 for p in draft.source_paths},
        summary=draft.summary,
        created_via=ProposalCreatedVia.SWEEP,
        detector="body_dup",
        finding_key=decision.finding_key,
        subject_key=decision.subject_key,
    )["id"]


def test_new_finding_creates_then_carries(repo):
    d = _deduper()
    first = d.decide("body_dup", _draft())
    assert first.action is DedupAction.CREATE
    pid = _persist(first, _draft())

    second = d.decide("body_dup", _draft())
    assert second.action is DedupAction.SKIP_LIVE
    assert second.existing_id == pid


def test_identity_survives_renames(repo):
    """Doc-id keying: the same pages at new paths are the same finding."""
    d = _deduper()
    key_before = d.finding_key("body_dup", _draft())

    _sha, moves = wiki_git.move_path("docs/b.md", "guides/b.md", "mv", author=None)
    doc_ids.on_path_moved(moves, root_move=PathMove(old="docs/b.md", new="guides/b.md"))

    moved = ProposalDraft(
        op=ProposalOp.MERGE,
        source_paths=["guides/b.md"],
        target_paths=["docs/a.md"],
        summary="merge b into a",
        premise="blob1",
    )
    key_after = _deduper().finding_key("body_dup", moved)
    assert key_after == key_before


def test_reserved_names_fall_back_to_casefolded_path(repo):
    d = _deduper()
    draft = ProposalDraft(
        op=ProposalOp.RENAME,
        source_paths=["docs/b.md", "docs/B-2.md"],  # B-2 doesn't exist
        target_paths=["docs/a.md"],
        summary="rename option",
    )
    assert "path:docs/b-2.md" in d.finding_key("case_collision", draft)


def test_rejected_finding_never_returns(repo):
    d = _deduper()
    first = d.decide("body_dup", _draft())
    pid = _persist(first, _draft())
    uid = seed_user(uid="u1", email="u@x.com")
    assert reject(pid, user_id=uid)

    again = d.decide("body_dup", _draft())
    assert again.action is DedupAction.SKIP_REJECTED
    assert again.existing_id == pid


def test_new_premise_on_rejected_subject_cools_down_then_asks(repo):
    d = _deduper()
    pid = _persist(d.decide("body_dup", _draft()), _draft())
    uid = seed_user(uid="u1", email="u@x.com")
    assert reject(pid, user_id=uid)

    fresh_content = _draft(premise="blob2")  # same pages, new shared content
    cooled = d.decide("body_dup", fresh_content)
    assert cooled.action is DedupAction.SKIP_COOLDOWN
    assert cooled.existing_id == pid

    # Window elapsed (cooldown disabled): the new situation may ask.
    eager = _deduper(cooldown_days=0)
    assert eager.decide("body_dup", fresh_content).action is DedupAction.CREATE


def test_stale_finding_revives_not_duplicates(repo):
    d = _deduper()
    pid = _persist(d.decide("body_dup", _draft()), _draft())
    assert mark_stale(pid, reason="drifted")

    again = d.decide("body_dup", _draft())
    assert again.action is DedupAction.REVIVE
    assert again.existing_id == pid


def test_different_detectors_are_different_findings(repo):
    d = _deduper()
    _persist(d.decide("body_dup", _draft()), _draft())
    other = d.decide("case_collision", _draft())
    assert other.action is DedupAction.CREATE


# --- runner integration: the real sweep carries and revives -----------------


@pytest.fixture
def sweep_repo(tmp_repo, tmp_db):
    wiki_git.commit_file("team/plan.md", "# Plan\n", "seed", author=None)
    wiki_git.commit_file("hollow/.gitkeep", "", "empty", author=None)
    return tmp_repo


@pytest.fixture(autouse=True)
def _eager_detector(monkeypatch):
    monkeypatch.setattr(runner, "DETECTORS", [_EmptyFolderDetector(min_age_days=0)])


def test_second_sweep_carries_instead_of_duplicating(sweep_repo):
    first = runner.run_sweep(triggered_by_user_id=None)
    assert first["proposals_emitted"] == 1
    from app.wiki.change_proposals import ProposalStatus, list_by_status

    (row,) = list_by_status(ProposalStatus.PENDING)
    # The identity snapshot is first-class and id-keyed: the stable term
    # indexes the map; the emit-time path is its label.
    assert row["doc_ids"] == {doc_ids.get_or_mint("hollow"): "hollow"}

    second = runner.run_sweep(triggered_by_user_id=None)
    assert second["proposals_emitted"] == 0  # carried, not re-created


def test_staled_proposal_revives_with_its_id(sweep_repo):
    first = runner.run_sweep(triggered_by_user_id=None)
    assert first["proposals_emitted"] == 1
    from app.wiki.change_proposals import ProposalStatus, list_by_status

    (row,) = list_by_status(ProposalStatus.PENDING)
    assert mark_stale(row["id"], reason="test drift")

    second = runner.run_sweep(triggered_by_user_id=None)
    assert second["proposals_emitted"] == 1  # revived counts as emitted

    revived = get(row["id"])
    assert revived is not None
    assert revived["status"] == ProposalStatus.PENDING.value
    assert revived["status_reason"] is None
    assert revived["run_id"] == second["run_id"]  # anchors refreshed
    # Same row, same identity — no sibling row was minted.
    assert len(list_by_status(ProposalStatus.PENDING)) == 1


def test_auto_approved_finding_blocks_re_emit(sweep_repo):
    """An applied row is a live status for identity purposes: the finding is
    resolved, not re-proposable. (Empty-folder auto-applies in AI-managed
    scopes; here it stays pending, so approve-and-apply is exercised at the
    unit level via SKIP_LIVE above — this guards the sweep-level contract
    that applied rows keep blocking.)"""
    first = runner.run_sweep(triggered_by_user_id=None)
    assert first["proposals_emitted"] == 1
    from app.wiki.change_proposals import ProposalStatus, list_by_status, mark_applied
    from app.wiki.change_proposals import approve as approve_row

    (row,) = list_by_status(ProposalStatus.PENDING)
    uid = seed_user(uid="u2", email="u2@x.com")
    assert approve_row(row["id"], user_id=uid)
    assert mark_applied(row["id"], applied_sha="0" * 40)

    # The folder still exists (nothing executed) — but the applied row owns
    # the finding, so the sweep does not re-emit it.
    second = runner.run_sweep(triggered_by_user_id=None)
    assert second["proposals_emitted"] == 0
