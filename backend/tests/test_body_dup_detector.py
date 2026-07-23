"""Exact body-duplicate detector + the runner's audience partitioning.

Detector: pure blob-sha grouping over the scope's pages — byte-identical
substantial bodies emit one merge draft per group (deterministic survivor,
human review required). Runner: pairing detectors see one same-audience
bucket at a time, so duplicates with different audiences are never paired.
"""
from __future__ import annotations

import pytest

from app.wiki import acl, update_policy
from app.wiki import git as wiki_git
from app.llm.agents import automanage_apply
from app.tasks.queues import automanage_nearline_queue
from app.wiki.automanage import review, runner
from app.wiki.automanage.detectors.base import Scope, TriggerKind
from app.wiki.automanage.detectors.body_dup import _BodyDupDetector
from app.wiki.change_proposals import (
    ProposalStatus,
    get as get_proposal,
    list_by_status,
)
from tests._seed import seed_user

# Substantial enough to clear MIN_BODY_BYTES.
_BODY = "# Setup Guide\n\n" + ("Run `make install` and configure the env.\n" * 4)


@pytest.fixture
def repo(tmp_repo):
    return tmp_repo


def _scope(*paths: str) -> Scope:
    return Scope(trigger=TriggerKind.SWEEP, paths=tuple(paths))


def _detect(*paths: str):
    return _BodyDupDetector().detect(_scope(*paths))


def test_identical_bodies_emit_one_merge_draft(repo):
    wiki_git.commit_file("docs/setup.md", _BODY, "seed", author=None)
    wiki_git.commit_file("guides/old/setup-copy.md", _BODY, "seed", author=None)
    wiki_git.commit_file("docs/other.md", "# Other\n" + "x" * 200, "seed", author=None)

    drafts = _detect("docs/setup.md", "guides/old/setup-copy.md", "docs/other.md")

    assert len(drafts) == 1
    d = drafts[0]
    assert d.op.value == "merge"
    assert d.target_paths == ["docs/setup.md"]  # shallower path survives
    assert d.source_paths == ["guides/old/setup-copy.md"]
    assert d.proposed_bodies == {"docs/setup.md": _BODY}
    assert d.auto_approvable is False  # merges wait for a human for now
    assert d.instruction and "byte-identical" in d.instruction


def test_three_way_duplicates_collapse_to_one_draft(repo):
    for p in ("a.md", "sub/b.md", "sub/deep/c.md"):
        wiki_git.commit_file(p, _BODY, "seed", author=None)

    drafts = _detect("a.md", "sub/b.md", "sub/deep/c.md")

    assert len(drafts) == 1
    assert drafts[0].target_paths == ["a.md"]
    assert sorted(drafts[0].source_paths) == ["sub/b.md", "sub/deep/c.md"]


def test_trivial_identical_bodies_are_not_duplicates(repo):
    # Byte-identical but tiny — placeholders, not duplicates.
    wiki_git.commit_file("a/todo.md", "# TODO\n", "seed", author=None)
    wiki_git.commit_file("b/todo.md", "# TODO\n", "seed", author=None)

    assert _detect("a/todo.md", "b/todo.md") == []


def test_only_scope_paths_are_considered(repo):
    wiki_git.commit_file("in/one.md", _BODY, "seed", author=None)
    wiki_git.commit_file("out/two.md", _BODY, "seed", author=None)

    # The duplicate exists in the repo but not in the scope → no pairing.
    assert _detect("in/one.md") == []


def test_runner_pairs_only_within_one_audience(repo):
    """Two identical pages with different audiences must not be paired — the
    proposal itself would leak the restricted page's existence."""
    uid = seed_user(uid="u1", email="u@x.com")
    wiki_git.commit_file("pub/a.md", _BODY, "seed", author=None)
    wiki_git.commit_file("pub/b.md", _BODY, "seed", author=None)
    wiki_git.commit_file("private/c.md", _BODY, "seed", author=None)
    # Restrict c.md: owner-only (drop nothing — owner row alone changes the
    # audience fingerprint away from implicit-public).
    acl.set_owner("private/c.md", uid)

    runner.run_sweep(triggered_by_user_id=None)

    pending = list_by_status(ProposalStatus.PENDING)
    merges = [p for p in pending if p["op"] == "merge"]
    assert len(merges) == 1
    touched = set(merges[0]["source_paths"] + merges[0]["target_paths"])
    assert touched == {"pub/a.md", "pub/b.md"}  # c.md never mentioned


def test_runner_emits_merge_with_fingerprint_and_instruction(repo):
    wiki_git.commit_file("x/a.md", _BODY, "seed", author=None)
    wiki_git.commit_file("x/b.md", _BODY, "seed", author=None)

    runner.run_sweep(triggered_by_user_id=None)

    merges = [
        p for p in list_by_status(ProposalStatus.PENDING) if p["op"] == "merge"
    ]
    assert len(merges) == 1
    p = merges[0]
    assert p["acl_fingerprint_before"]
    assert p["instruction"] and "retire" in p["instruction"]
    assert p["proposed_bodies"] == {"x/a.md": _BODY}


def test_merge_never_auto_applies_even_in_ai_scope(repo):
    wiki_git.commit_file("ai/a.md", _BODY, "seed", author=None)
    wiki_git.commit_file("ai/b.md", _BODY, "seed", author=None)
    update_policy.set_policy("ai", ai_management_allowed=True)

    runner.run_sweep(triggered_by_user_id=None)

    merges = [
        p
        for p in list_by_status(ProposalStatus.PENDING)
        if p["op"] == "merge"
    ]
    assert len(merges) == 1  # pending — auto_approvable=False held even here
    assert list_by_status(ProposalStatus.APPLIED) == []
# --------------------------------------------------------------------------- #
# Premise re-validation (Detector.validate)                                   #
# --------------------------------------------------------------------------- #


def _pending_merge():
    merges = [
        p for p in list_by_status(ProposalStatus.PENDING) if p["op"] == "merge"
    ]
    assert len(merges) == 1
    return merges[0]


def test_validate_holds_while_bodies_identical(repo):
    wiki_git.commit_file("v/a.md", _BODY, "seed", author=None)
    wiki_git.commit_file("v/b.md", _BODY, "seed", author=None)
    runner.run_sweep(triggered_by_user_id=None)
    p = _pending_merge()

    assert p["detector"] == "body_dup"  # runner stamped the author
    assert _BodyDupDetector().validate(p) is None


def test_validate_survives_an_edit_applied_to_both_copies(repo):
    """Drift is not invalidity: the same fix landing on both copies keeps them
    byte-identical, so the proposal's premise — duplicate — still holds."""
    wiki_git.commit_file("v/a.md", _BODY, "seed", author=None)
    wiki_git.commit_file("v/b.md", _BODY, "seed", author=None)
    runner.run_sweep(triggered_by_user_id=None)
    p = _pending_merge()

    fixed = _BODY.replace("configure the env", "configure the environment")
    wiki_git.commit_file("v/a.md", fixed, "fix typo", author=None)
    wiki_git.commit_file("v/b.md", fixed, "fix typo", author=None)

    assert _BodyDupDetector().validate(p) is None  # still duplicates


def test_validate_stales_when_the_survivor_diverges(repo):
    wiki_git.commit_file("v/a.md", _BODY, "seed", author=None)
    wiki_git.commit_file("v/b.md", _BODY, "seed", author=None)
    runner.run_sweep(triggered_by_user_id=None)
    p = _pending_merge()

    # The survivor (target) gains new content — the pages are distinct now.
    wiki_git.commit_file("v/a.md", _BODY + "\nNew section.\n", "edit", author=None)

    reason = _BodyDupDetector().validate(p)
    assert reason is not None and "no longer byte-identical" in reason


def test_executor_stales_a_diverged_merge_without_calling_the_llm(repo, monkeypatch):
    wiki_git.commit_file("v/a.md", _BODY, "seed", author=None)
    wiki_git.commit_file("v/b.md", _BODY, "seed", author=None)
    runner.run_sweep(triggered_by_user_id=None)
    p = _pending_merge()

    # Diverge after detection, then approve and execute.
    wiki_git.commit_file("v/a.md", _BODY + "\nDrift.\n", "edit", author=None)

    def must_not_run(*a, **k):
        raise AssertionError("LLM must not run on an invalid premise")

    monkeypatch.setattr(automanage_apply.client, "complete", must_not_run)
    with automanage_nearline_queue.immediate_mode():
        review.approve(p["id"], user_id=seed_user(uid="rv", email="rv@x.com"))

    refreshed = get_proposal(p["id"])
    assert refreshed is not None
    assert refreshed["status"] == "stale"
    assert "byte-identical" in (refreshed["status_reason"] or "")
    assert "v/b.md" in wiki_git.list_paths()  # nothing retired
