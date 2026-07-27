"""The on-create trigger — focused detection for a just-created page.

Only instant-truth detectors run (case-collision, body-dup; settle-dependent
detectors stay sweep-only), scoped to the page's creation neighborhood, and
only findings involving the new page emit. The lifecycle hook enqueues the
run for attributable creations only — system channels (ingestion, seeds)
pass ``owner_user_id=None`` and are skipped; the sweep covers them.
"""
from __future__ import annotations

from app.models.wiki import ChangeKind
from app.tasks.queues import automanage_offline_queue
from app.wiki import git as wiki_git
from app.wiki import notify
from app.wiki.automanage import runner, runs
from app.wiki.automanage.detectors.base import TriggerKind
from app.wiki.change_proposals import (
    ProposalCreatedVia,
    ProposalOp,
    ProposalStatus,
    create as create_proposal,
    list_by_status,
    reject,
)
from tests._seed import plumb_commit, seed_user

# Bodies comfortably above body-dup's placeholder floor.
_BODY = "# Setup\n\nInstall steps for the app.\n" + "content " * 40
_OTHER = "# Notes\n\nEntirely different material.\n" + "words " * 40


def _pendings() -> list[dict]:
    return list_by_status(ProposalStatus.PENDING, limit=None)


def test_duplicate_create_emits_a_focused_proposal(tmp_repo):
    wiki_git.commit_file("team/original.md", _BODY, "seed", author=None)
    wiki_git.commit_file("team/copy.md", _BODY, "seed dup", author=None)

    result = runner.run_on_create("team/copy.md", triggered_by_user_id=None)

    assert result["proposals_emitted"] == 1
    (row,) = _pendings()
    assert row["detector"] == "body_dup"
    assert row["created_via"] == ProposalCreatedVia.ON_CREATE.value
    assert "team/copy.md" in row["source_paths"] + row["target_paths"]
    (run_row,) = runs.list_recent()
    assert run_row["trigger"] == TriggerKind.ON_CREATE.value


def test_common_create_skips_the_run_entirely(tmp_repo):
    """A page that collides with nothing and duplicates nothing — the
    overwhelmingly common create — must not even record a run row."""
    wiki_git.commit_file("team/original.md", _BODY, "seed", author=None)
    wiki_git.commit_file("team/unique.md", _OTHER, "seed", author=None)

    result = runner.run_on_create("team/unique.md", triggered_by_user_id=None)

    assert result == {"run_id": None, "paths_scanned": 0, "proposals_emitted": 0}
    assert runs.list_recent() == []
    assert _pendings() == []


def test_case_collision_on_create(tmp_repo):
    wiki_git.commit_file("docs/Setup.md", _BODY, "seed", author=None)
    plumb_commit("docs/setup.md", _OTHER)

    result = runner.run_on_create("docs/setup.md", triggered_by_user_id=None)

    assert result["proposals_emitted"] == 1
    (row,) = _pendings()
    assert row["detector"] == "case_collision"
    assert "docs/setup.md" in row["source_paths"] + row["target_paths"]


def test_focus_drops_findings_not_involving_the_new_page(tmp_repo):
    """The focus filter is the scope-bounding net: a finding among the
    neighbors that doesn't involve the created page is the sweep's business,
    not this run's."""
    wiki_git.commit_file("a/one.md", _BODY, "seed", author=None)
    wiki_git.commit_file("a/two.md", _BODY, "seed dup", author=None)

    result = runner.run_detection(
        trigger=TriggerKind.ON_CREATE,
        triggered_by_user_id=None,
        paths=["a/one.md", "a/two.md"],
        focus_paths=frozenset({"elsewhere/new.md"}),
    )

    assert result["proposals_emitted"] == 0
    assert _pendings() == []


def test_settle_dependent_detectors_stay_sweep_only(tmp_repo):
    """Two tiny identical pages: below body-dup's floor, and stub/template
    findings must not fire at creation (every new page starts as a stub)."""
    wiki_git.commit_file("t/a.md", "# a\n", "seed", author=None)
    wiki_git.commit_file("t/b.md", "# a\n", "seed dup", author=None)

    result = runner.run_on_create("t/b.md", triggered_by_user_id=None)

    assert result["proposals_emitted"] == 0
    assert _pendings() == []


def test_focused_run_never_invalidates_the_ledger(tmp_repo):
    """Reconciliation is sweep-only: a pending row about unrelated pages must
    survive a focused run untouched (a partial scope's silence proves
    nothing)."""
    wiki_git.commit_file("keep/page.md", _OTHER, "seed", author=None)
    unrelated = create_proposal(
        op=ProposalOp.DELETE_PAGE,
        source_paths=["keep/page.md"],
        target_paths=[],
        base_shas={},
        summary="unrelated pending",
        created_via=ProposalCreatedVia.SWEEP,
        detector="stub_page",
        dedup_key="stub_page|delete_page|unrelated|",
    )
    wiki_git.commit_file("team/original.md", _BODY, "seed", author=None)
    wiki_git.commit_file("team/copy.md", _BODY, "seed dup", author=None)

    runner.run_on_create("team/copy.md", triggered_by_user_id=None)

    ids = {r["id"] for r in _pendings()}
    assert unrelated["id"] in ids  # still pending, not invalidated


def test_rejection_suppresses_the_recreated_ask(tmp_repo):
    uid = seed_user(uid="u1", email="u@x.com")
    wiki_git.commit_file("team/original.md", _BODY, "seed", author=None)
    wiki_git.commit_file("team/copy.md", _BODY, "seed dup", author=None)
    result = runner.run_on_create("team/copy.md", triggered_by_user_id=uid)
    (row,) = _pendings()
    assert result["proposals_emitted"] == 1
    assert reject(row["id"], user_id=uid)

    rerun = runner.run_on_create("team/copy.md", triggered_by_user_id=uid)

    assert rerun["proposals_emitted"] == 0
    assert _pendings() == []


def test_create_hook_enqueues_for_attributable_creations_only(tmp_repo):
    uid = seed_user(uid="u1", email="u@x.com")
    wiki_git.commit_file("team/original.md", _BODY, "seed", author=None)

    # System channel (owner_user_id=None): no focused run.
    sha = wiki_git.commit_file("team/sys-copy.md", _BODY, "ingest", author=None)
    with automanage_offline_queue.immediate_mode():
        notify.after_doc_write(
            "team/sys-copy.md", sha, ChangeKind.CREATE, None, owner_user_id=None
        )
    assert _pendings() == []

    # Attributable creation: the hook runs focused detection.
    sha = wiki_git.commit_file("team/user-copy.md", _BODY, "create", author=None)
    with automanage_offline_queue.immediate_mode():
        notify.after_doc_write(
            "team/user-copy.md", sha, ChangeKind.CREATE, None, owner_user_id=uid
        )
    rows = _pendings()
    assert len(rows) == 1
    assert "team/user-copy.md" in rows[0]["source_paths"] + rows[0]["target_paths"]
