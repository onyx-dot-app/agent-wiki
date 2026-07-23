"""Case-insensitive path-collision detector + the rename execution path.

Pure path analysis: pages whose paths differ only by case propose a rename of
the non-canonical one — unless they're byte-identical (body-dup's merge
resolves those). Also covers the two enablers this op needed: target
anchoring tolerates brand-new paths, and a moved id (still live at its new
path) is not "removed without forward".
"""
from __future__ import annotations

import os
import subprocess
from typing import Any

import pytest

import app.config

from app.llm.agents import automanage_apply
from app.llm.client import CompletionResult, ToolCall
from app.tasks.queues import automanage_nearline_queue
from app.wiki import doc_ids
from app.wiki import git as wiki_git
from app.wiki.automanage import review, runner
from app.wiki.automanage.detectors.base import Scope, TriggerKind
from app.wiki.automanage.detectors.case_collision import _CaseCollisionDetector
from app.wiki.change_proposals import (
    ProposalStatus,
    get as get_proposal,
    list_by_status,
)
from tests._seed import seed_user

_A = "# Setup\n\nInstall steps for the app.\n" + "a" * 120
_B = "# setup (draft)\n\nCompletely different notes.\n" + "b" * 120


@pytest.fixture
def repo(tmp_repo, tmp_config):
    return tmp_config


def _fs_case_insensitive(tmp_path_factory=None) -> bool:
    """True on filesystems (macOS/Windows defaults) that can't hold both
    casings of a path — exactly the hazard this detector exists for."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        probe = os.path.join(d, "CaseProbe")
        open(probe, "w").write("x")
        return os.path.exists(os.path.join(d, "caseprobe"))


def _plumb_commit(path: str, body: str) -> None:
    """Commit ``path`` via git plumbing (index + objects only): the one way to
    stage case-colliding paths on a case-insensitive dev filesystem, where a
    worktree write would silently overwrite the other casing. Test seeding
    only — the detector itself reads the index/objects, never the worktree."""
    cwd = app.config.CONFIG.wiki_dir
    def run(*args: str, inp: str | None = None) -> str:
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
            input=inp,
        ).stdout.strip()
    blob = run("hash-object", "-w", "--stdin", inp=body)
    run("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}")
    tree = run("write-tree")
    head = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"], cwd=cwd,
        capture_output=True, text=True,
    ).stdout.strip()
    parent = ["-p", head] if head else []
    commit = run("commit-tree", tree, *parent, "-m", f"seed {path}")
    run("update-ref", "HEAD", commit)


def _scope(*paths: str) -> Scope:
    return Scope(trigger=TriggerKind.SWEEP, paths=tuple(paths))


def test_distinct_content_collision_proposes_rename(repo):
    _plumb_commit("docs/Setup.md", _A)
    _plumb_commit("docs/setup.md", _B)

    drafts = _CaseCollisionDetector().detect(_scope("docs/Setup.md", "docs/setup.md"))

    assert len(drafts) == 1
    d = drafts[0]
    assert d.op.value == "rename"
    # Survivor heuristic keeps "docs/Setup.md" (uppercase sorts first at equal
    # depth/length); the loser + its deconflicted rename option are sources,
    # the kept page is the target that must survive either outcome.
    assert d.source_paths == ["docs/setup.md", "docs/setup-2.md"]
    assert d.target_paths == ["docs/Setup.md"]
    assert "rename" in d.summary and "merge" in d.summary
    assert d.instruction and "move_page" in d.instruction and "retire_page" in d.instruction
    assert d.auto_approvable is False


def test_identical_content_collision_is_left_to_body_dup(repo):
    _plumb_commit("docs/Guide.md", _A)
    _plumb_commit("docs/guide.md", _A)

    assert _CaseCollisionDetector().detect(_scope("docs/Guide.md", "docs/guide.md")) == []


def test_deconflicted_name_avoids_new_collisions(repo):
    _plumb_commit("n/Page.md", _A)
    _plumb_commit("n/page.md", _B)
    _plumb_commit("n/PAGE-2.md", "occupied " + "c" * 120)

    (d,) = _CaseCollisionDetector().detect(
        _scope("n/Page.md", "n/page.md", "n/PAGE-2.md")
    )
    assert d.source_paths[1] == "n/page-3.md"  # -2 taken case-insensitively


def test_validate_tracks_the_premise(repo):
    _plumb_commit("v/Doc.md", _A)
    _plumb_commit("v/doc.md", _B)
    det = _CaseCollisionDetector()
    (d,) = det.detect(_scope("v/Doc.md", "v/doc.md"))
    proposal: dict[str, Any] = {
        "source_paths": d.source_paths,
        "target_paths": d.target_paths,
    }

    assert det.validate(proposal) is None

    # Kept page removed → no collision left (plumbing removal).
    subprocess.run(
        ["git", "update-index", "--force-remove", "v/Doc.md"],
        cwd=app.config.CONFIG.wiki_dir, check=True, capture_output=True,
    )
    tree = subprocess.run(
        ["git", "write-tree"], cwd=app.config.CONFIG.wiki_dir, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=app.config.CONFIG.wiki_dir, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    commit = subprocess.run(
        ["git", "commit-tree", tree, "-p", head, "-m", "remove"],
        cwd=app.config.CONFIG.wiki_dir, check=True, capture_output=True, text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "HEAD", commit],
        cwd=app.config.CONFIG.wiki_dir, check=True, capture_output=True,
    )
    reason = det.validate(proposal)
    assert reason is not None and "no longer exists" in reason


def test_validate_stays_valid_when_pages_converge(repo):
    """Pages that became identical while pending don't stale the proposal —
    the applier's merge branch resolves them."""
    _plumb_commit("c/Doc.md", _A)
    _plumb_commit("c/doc.md", _B)
    det = _CaseCollisionDetector()
    (d,) = det.detect(_scope("c/Doc.md", "c/doc.md"))
    proposal: dict[str, Any] = {
        "source_paths": d.source_paths,
        "target_paths": d.target_paths,
    }
    _plumb_commit("c/doc.md", _A)  # now byte-identical to the kept page
    assert det.validate(proposal) is None


@pytest.mark.skipif(
    _fs_case_insensitive(),
    reason="worktree cannot hold both casings on this filesystem — the very "
    "hazard the detector fixes; the end-to-end runs on case-sensitive CI",
)
def test_rename_executes_end_to_end(repo, monkeypatch):
    """Runner anchors a brand-new target path (enabler 1), the applier renames
    via move_page, and the moved id — live at its new path — passes the
    forward check (enabler 2)."""
    monkeypatch.setattr(runner, "DETECTORS", [_CaseCollisionDetector()])
    _plumb_commit("e/Notes.md", _A)
    _plumb_commit("e/notes.md", _B)
    subprocess.run(["git", "checkout", "--", "."], cwd=app.config.CONFIG.wiki_dir, check=True, capture_output=True)
    loser_id = doc_ids.get_or_mint("e/notes.md")

    runner.run_sweep(triggered_by_user_id=None)
    pending = list_by_status(ProposalStatus.PENDING)
    assert len(pending) == 1  # the anchoring fix: draft not skipped
    pid = pending[0]["id"]
    assert pending[0]["detector"] == "case_collision"
    assert pending[0]["target_paths"] == ["e/Notes.md"]

    turns = [
        CompletionResult(
            tool_calls=[
                ToolCall(
                    id="t1",
                    name="move_page",
                    arguments={"source": "e/notes.md", "dest": "e/notes-2.md"},
                )
            ]
        ),
        CompletionResult(text="Renamed to resolve the case collision."),
    ]
    monkeypatch.setattr(
        automanage_apply.client, "complete", lambda messages, **kw: turns.pop(0)
    )
    uid = seed_user(uid="rv", email="rv@x.com")
    with automanage_nearline_queue.immediate_mode():
        review.approve(pid, user_id=uid)

    p = get_proposal(pid)
    assert p is not None and p["status"] == "applied"
    live = set(wiki_git.list_paths())
    assert "e/notes-2.md" in live and "e/notes.md" not in live
    assert "e/Notes.md" in live  # kept page untouched
    resolved = doc_ids.resolve(loser_id)
    assert resolved is not None
    assert resolved["path"] == "e/notes-2.md"  # id moved with the page
    assert resolved["deleted_at"] is None
