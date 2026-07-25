"""Selection + reconciliation — step 3 of the sweep pipeline.

Pure claim mechanics first (no DB), then runner-level behavior: one live
proposal per page with stability-first selection, unselected findings
persisted invalid, pendings that stopped being true invalidated, and the
post-rejection cooldown in its selection-step home (persisted but
unselectable; same-premise rejection stays dedup's forever-suppression).
"""
from __future__ import annotations

import pytest

from app.wiki import git as wiki_git
from app.wiki.automanage import runner, selection
from app.wiki.automanage.detectors.base import ProposalDraft, Scope, TriggerKind
from app.wiki.change_proposals import (
    ProposalOp,
    ProposalStatus,
    approve,
    get,
    list_by_status,
    reject,
)
from tests._seed import seed_user

# --- pure claim mechanics ----------------------------------------------------


def test_claims_conflict_on_equal_paths_case_insensitively():
    a = selection.claim_of(["docs/Setup.md"])
    b = selection.claim_of(["docs/setup.md"])
    assert selection.conflicts(a, b)


def test_claims_conflict_subtree_both_directions():
    folder = selection.claim_of(["team"])
    page = selection.claim_of(["team/notes/plan.md"])
    assert selection.conflicts(page, folder)
    assert selection.conflicts(folder, page)


def test_disjoint_claims_pass():
    a = selection.claim_of(["team/a.md", "guides"])
    b = selection.claim_of(["docs/b.md", "teammates/c.md"])
    assert not selection.conflicts(a, b)


# --- runner-level: reconciliation + slate -------------------------------------


class _FixedDetector:
    """Emits a fixed draft list — lets tests stage same-page contention and
    disappearing findings without real detector mechanics."""

    pairs_paths = False

    def __init__(self, name: str, drafts: list[ProposalDraft]) -> None:
        self.name = name
        self.drafts = drafts

    def applicable(self, trigger: TriggerKind) -> bool:
        return trigger is TriggerKind.SWEEP

    def detect(self, scope: Scope) -> list[ProposalDraft]:
        return list(self.drafts)

    def validate(self, proposal) -> str | None:
        return None


def _stub_draft(path: str, premise: str | None = None) -> ProposalDraft:
    return ProposalDraft(
        op=ProposalOp.DELETE_PAGE,
        source_paths=[path],
        summary=f"remove {path}",
        premise=premise,
        auto_approvable=False,
    )


@pytest.fixture
def repo(tmp_repo, tmp_db):
    wiki_git.commit_file("team/a.md", "# A\n", "seed", author=None)
    wiki_git.commit_file("team/b.md", "# B\n", "seed", author=None)
    return tmp_repo


def _sweep(monkeypatch, detectors) -> dict:
    monkeypatch.setattr(runner, "DETECTORS", detectors)
    return runner.run_sweep(triggered_by_user_id=None)


def test_one_live_proposal_per_page(repo, monkeypatch):
    """Two detectors, same page: registry order wins; the loser is persisted
    invalid with a not-selected reason — recorded, not lost."""
    first = _FixedDetector("det_one", [_stub_draft("team/a.md")])
    second = _FixedDetector("det_two", [_stub_draft("team/a.md", premise="x")])

    result = _sweep(monkeypatch, [first, second])

    assert result["proposals_emitted"] == 1
    (pending,) = list_by_status(ProposalStatus.PENDING)
    assert pending["detector"] == "det_one"
    (invalid,) = list_by_status(ProposalStatus.STALE)
    assert invalid["detector"] == "det_two"
    assert "not selected" in (invalid["status_reason"] or "")


def test_unselected_finding_revives_once_the_page_frees(repo, monkeypatch):
    first = _FixedDetector("det_one", [_stub_draft("team/a.md")])
    second = _FixedDetector("det_two", [_stub_draft("team/a.md", premise="x")])
    _sweep(monkeypatch, [first, second])
    (pending,) = list_by_status(ProposalStatus.PENDING)
    (invalid,) = list_by_status(ProposalStatus.STALE)

    uid = seed_user(uid="u1", email="u@x.com")
    assert reject(pending["id"], user_id=uid)  # frees the page

    # det_one gone (its ask was rejected); det_two re-detects.
    result = _sweep(monkeypatch, [_FixedDetector("det_two", [_stub_draft("team/a.md", premise="x")])])
    assert result["proposals_emitted"] == 1
    revived = get(invalid["id"])
    assert revived is not None and revived["status"] == "pending"
    assert revived["revive_count"] == 1  # same row came back — no sibling


def test_pending_not_redetected_is_invalidated(repo, monkeypatch):
    det = _FixedDetector("det_one", [_stub_draft("team/a.md")])
    _sweep(monkeypatch, [det])
    (pending,) = list_by_status(ProposalStatus.PENDING)

    # Next sweep: the finding no longer exists (detector sees nothing).
    _sweep(monkeypatch, [_FixedDetector("det_one", [])])

    row = get(pending["id"])
    assert row is not None and row["status"] == "stale"
    assert "not re-detected" in (row["status_reason"] or "")
    assert list_by_status(ProposalStatus.PENDING) == []


def test_approved_rows_are_untouchable_and_hold_their_claim(repo, monkeypatch):
    det = _FixedDetector("det_one", [_stub_draft("team/a.md")])
    _sweep(monkeypatch, [det])
    (pending,) = list_by_status(ProposalStatus.PENDING)
    uid = seed_user(uid="u1", email="u@x.com")
    assert approve(pending["id"], user_id=uid)

    # The finding vanished AND a rival wants the page: the approved row is
    # neither invalidated nor bumped; the rival is persisted invalid.
    _sweep(monkeypatch, [_FixedDetector("det_two", [_stub_draft("team/a.md", premise="x")])])

    row = get(pending["id"])
    assert row is not None and row["status"] == "approved"
    (invalid,) = list_by_status(ProposalStatus.STALE)
    assert invalid["detector"] == "det_two"


def test_carried_pending_outranks_new_contender(repo, monkeypatch):
    det = _FixedDetector("det_one", [_stub_draft("team/a.md")])
    _sweep(monkeypatch, [det])
    (pending,) = list_by_status(ProposalStatus.PENDING)

    # Same finding still true; a new detector also wants the page. Even
    # listed first in the registry, the newcomer must not bump the
    # already-surfaced ask.
    rival = _FixedDetector("det_zero", [_stub_draft("team/a.md", premise="x")])
    result = _sweep(monkeypatch, [rival, det])

    assert result["proposals_emitted"] == 0  # carried isn't re-emitted
    row = get(pending["id"])
    assert row is not None and row["status"] == "pending"
    (invalid,) = list_by_status(ProposalStatus.STALE)
    assert invalid["detector"] == "det_zero"


def test_new_premise_after_rejection_cools_down_then_asks(repo, monkeypatch):
    det = _FixedDetector("det_one", [_stub_draft("team/a.md")])
    _sweep(monkeypatch, [det])
    (pending,) = list_by_status(ProposalStatus.PENDING)
    uid = seed_user(uid="u1", email="u@x.com")
    assert reject(pending["id"], user_id=uid)

    # Different premise, same detector+op+page, right after the "no":
    # persisted but unselectable.
    fresh = _FixedDetector("det_one", [_stub_draft("team/a.md", premise="new")])
    result = _sweep(monkeypatch, [fresh])
    assert result["proposals_emitted"] == 0
    cooled = [
        r for r in list_by_status(ProposalStatus.STALE)
        if "cooldown" in (r["status_reason"] or "")
    ]
    assert len(cooled) == 1

    # Still inside the window, the next sweep re-detects the cooled row —
    # it must stay at rest, not slip back in through the revive path.
    result = _sweep(monkeypatch, [fresh])
    assert result["proposals_emitted"] == 0
    still = get(cooled[0]["id"])
    assert still is not None and still["status"] == "stale"

    # Window elapsed: the same finding revives into the queue.
    monkeypatch.setattr(selection, "SUBJECT_COOLDOWN_DAYS", 0)
    result = _sweep(monkeypatch, [fresh])
    assert result["proposals_emitted"] == 1
    row = get(cooled[0]["id"])
    assert row is not None and row["status"] == "pending"
    assert row["revive_count"] == 1


def test_folder_claim_locks_pages_beneath_it(repo, monkeypatch):
    folder_det = _FixedDetector(
        "det_folder",
        [
            ProposalDraft(
                op=ProposalOp.DELETE_EMPTY_FOLDER,
                source_paths=["team"],
                summary="remove folder team",
                auto_approvable=False,
            )
        ],
    )
    page_det = _FixedDetector("det_page", [_stub_draft("team/a.md")])

    result = _sweep(monkeypatch, [folder_det, page_det])

    assert result["proposals_emitted"] == 1
    (pending,) = list_by_status(ProposalStatus.PENDING)
    assert pending["detector"] == "det_folder"
    (invalid,) = list_by_status(ProposalStatus.STALE)
    assert invalid["detector"] == "det_page"


def test_cap_bounds_total_writes_including_invalid(repo, monkeypatch):
    """The per-run cap counts persisted-invalid rows too — a pathological
    sweep can't flood the ledger through the not-selected path."""
    monkeypatch.setattr(runner, "MAX_PROPOSALS_PER_RUN", 2)
    contenders = [
        _FixedDetector("det_one", [_stub_draft("team/a.md")]),
        _FixedDetector("det_two", [_stub_draft("team/a.md", premise="x")]),
        _FixedDetector("det_three", [_stub_draft("team/a.md", premise="y")]),
        _FixedDetector("det_four", [_stub_draft("team/a.md", premise="z")]),
    ]
    result = _sweep(monkeypatch, contenders)

    assert result["proposals_emitted"] == 1  # one selected
    stale = list_by_status(ProposalStatus.STALE)
    assert len(stale) == 1  # one invalid persisted, then the cap cut off
