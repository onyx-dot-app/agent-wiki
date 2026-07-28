"""Misplaced-page detector — LLM detector #2, strays-only.

The prefilter admits only quiet root-level pages (never entry points); the
scripted LLM (patched at the ``client.complete`` seam) proposes filings; the
mechanical guards drop anything outside the rails — non-candidates, invented
or empty destinations, taken targets. validate() re-checks the page and the
destination.
"""
from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from typing import Any


import app.config
from app.llm import client as llm_client
from app.llm.client import CompletionResult, ToolCall, Usage
from app.llm.errors import LLMError
from app.wiki import git as wiki_git
from app.wiki.automanage.detectors.base import Scope, TriggerKind
from app.wiki.automanage.detectors.misplaced_page import _MisplacedPageDetector
from app.wiki.change_proposals import ProposalOp

_BODY = "# Deploy checklist\n\nSteps referenced by the runbooks.\n" + "step " * 30


def _backdated_commit(path: str, body: str, days: int = 60) -> None:
    """Commit with old author+committer dates (test seeding only)."""
    import os

    cwd = app.config.CONFIG.wiki_dir
    when = (datetime.now(UTC) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )
    absd = os.path.join(cwd, os.path.dirname(path))
    if os.path.dirname(path):
        os.makedirs(absd, exist_ok=True)
    with open(os.path.join(cwd, path), "w") as f:
        f.write(body)
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    subprocess.run(["git", "add", path], cwd=cwd, check=True, env=env)
    subprocess.run(
        ["git", "-c", "user.name=seed", "-c", "user.email=seed@local",
         "commit", "-m", f"seed {path}"],
        cwd=cwd, check=True, env=env, capture_output=True,
    )


def _scope(*paths: str) -> Scope:
    return Scope(trigger=TriggerKind.SWEEP, paths=tuple(paths))


def _finish(*proposals: dict[str, str]) -> CompletionResult:
    return CompletionResult(
        text="",
        tool_calls=[
            ToolCall(id="c1", name="finish", arguments={"proposals": list(proposals)})
        ],
        stop_reason="tool_use",
        usage=Usage(),
    )


def _script(monkeypatch, *results: CompletionResult) -> list[list[dict[str, Any]]]:
    calls: list[list[dict[str, Any]]] = []
    queue = list(results)

    def fake(messages, **kwargs):
        calls.append(messages)
        return queue.pop(0)

    monkeypatch.setattr(llm_client, "complete", fake)
    return calls


def _seed_home() -> None:
    """A real destination folder with pages."""
    wiki_git.commit_file("Runbooks/restore.md", "# Restore\nsteps\n", "seed", author=None)
    wiki_git.commit_file("Runbooks/rotate.md", "# Rotate\nsteps\n", "seed", author=None)


# ---- prefilter ------------------------------------------------------------ #


def test_nested_pages_are_never_candidates(monkeypatch, tmp_repo):
    _seed_home()
    _backdated_commit("Runbooks/old.md", _BODY)
    calls = _script(monkeypatch)
    det = _MisplacedPageDetector()
    assert det.detect(_scope("Runbooks/old.md", "Runbooks/restore.md")) == []
    assert calls == []


def test_entry_points_are_never_candidates(monkeypatch, tmp_repo):
    _seed_home()
    for name in ("README.md", "Home.md", "index.md", "Start Here.md"):
        _backdated_commit(name, _BODY)
    calls = _script(monkeypatch)
    det = _MisplacedPageDetector()
    scope = _scope("README.md", "Home.md", "index.md", "Start Here.md",
                   "Runbooks/restore.md", "Runbooks/rotate.md")
    assert det.detect(scope) == []
    assert calls == []


def test_recently_edited_root_pages_are_excluded(monkeypatch, tmp_repo):
    _seed_home()
    wiki_git.commit_file("fresh.md", _BODY, "seed", author=None)  # edited now
    calls = _script(monkeypatch)
    det = _MisplacedPageDetector()
    assert det.detect(_scope("fresh.md", "Runbooks/restore.md")) == []
    assert calls == []


def test_no_folders_means_no_candidates(monkeypatch, tmp_repo):
    _backdated_commit("stray.md", _BODY)
    calls = _script(monkeypatch)
    det = _MisplacedPageDetector()
    assert det.detect(_scope("stray.md")) == []
    assert calls == []


# ---- agent pass + mechanical guards ---------------------------------------- #


def _stray_scope() -> Scope:
    return _scope("stray.md", "Runbooks/restore.md", "Runbooks/rotate.md")


def test_confirmed_stray_emits_a_move_draft(monkeypatch, tmp_repo):
    _seed_home()
    _backdated_commit("stray.md", _BODY)
    _script(
        monkeypatch,
        _finish({"path": "stray.md", "dest_folder": "Runbooks",
                 "evidence": "deploy checklist referencing the runbooks"}),
    )

    (draft,) = _MisplacedPageDetector().detect(_stray_scope())

    assert draft.op == ProposalOp.MOVE
    assert draft.source_paths == ["stray.md"]
    assert draft.target_paths == ["Runbooks/stray.md"]
    assert "deploy checklist" in draft.summary
    assert draft.auto_approvable is False
    meta = wiki_git.last_commit_meta_for_path("stray.md")
    assert meta is not None and draft.premise == meta[0]


def test_invented_destination_is_dropped(monkeypatch, tmp_repo):
    _seed_home()
    _backdated_commit("stray.md", _BODY)
    _script(
        monkeypatch,
        _finish({"path": "stray.md", "dest_folder": "Made Up Folder",
                 "evidence": "x"}),
    )
    assert _MisplacedPageDetector().detect(_stray_scope()) == []


def test_taken_target_is_dropped(monkeypatch, tmp_repo):
    _seed_home()
    _backdated_commit("stray.md", _BODY)
    # Occupy the target with a different casing — the guard is
    # case-insensitive, matching the filesystem hazard.
    wiki_git.commit_file("Runbooks/STRAY.md", "# other\n", "seed", author=None)
    _script(
        monkeypatch,
        _finish({"path": "stray.md", "dest_folder": "Runbooks", "evidence": "x"}),
    )
    scope = _scope("stray.md", "Runbooks/restore.md", "Runbooks/rotate.md",
                   "Runbooks/STRAY.md")
    assert _MisplacedPageDetector().detect(scope) == []


def test_cap_bounds_proposals(monkeypatch, tmp_repo):
    _seed_home()
    items = []
    paths = [f"stray-{i}.md" for i in range(4)]
    for p in paths:
        _backdated_commit(p, _BODY + p)
        items.append({"path": p, "dest_folder": "Runbooks", "evidence": "x"})
    _script(monkeypatch, _finish(*items))
    scope = _scope(*paths, "Runbooks/restore.md", "Runbooks/rotate.md")
    drafts = _MisplacedPageDetector().detect(scope)
    assert len(drafts) == 2  # MAX_PROPOSALS


def test_llm_failure_degrades_to_empty(monkeypatch, tmp_repo):
    _seed_home()
    _backdated_commit("stray.md", _BODY)

    def boom(messages, **kwargs):
        raise LLMError("not_configured", "LLM is not configured")

    monkeypatch.setattr(llm_client, "complete", boom)
    assert _MisplacedPageDetector().detect(_stray_scope()) == []


# ---- validate --------------------------------------------------------------- #


def _proposal_for(path: str, target: str) -> dict[str, Any]:
    meta = wiki_git.last_commit_meta_for_path(path)
    assert meta is not None
    return {
        "source_paths": [path],
        "target_paths": [target],
        "base_shas": {path: meta[0]},
    }


def test_validate_holds_while_nothing_happened(tmp_repo):
    _seed_home()
    _backdated_commit("stray.md", _BODY)
    p = _proposal_for("stray.md", "Runbooks/stray.md")
    assert _MisplacedPageDetector().validate(p) is None


def test_validate_stales_on_edit(tmp_repo):
    _seed_home()
    _backdated_commit("stray.md", _BODY)
    p = _proposal_for("stray.md", "Runbooks/stray.md")
    wiki_git.commit_file("stray.md", _BODY + "update\n", "edit", author=None)
    assert _MisplacedPageDetector().validate(p) == (
        "the page changed since it was judged misplaced"
    )


def test_validate_stales_when_target_taken(tmp_repo):
    _seed_home()
    _backdated_commit("stray.md", _BODY)
    p = _proposal_for("stray.md", "Runbooks/stray.md")
    wiki_git.commit_file("Runbooks/STRAY.md", "# other\n", "seed", author=None)
    assert _MisplacedPageDetector().validate(p) == (
        "the destination path is taken now"
    )


def test_validate_stales_when_destination_empties(tmp_repo):
    wiki_git.commit_file("Runbooks/only.md", "# only\n", "seed", author=None)
    _backdated_commit("stray.md", _BODY)
    p = _proposal_for("stray.md", "Runbooks/stray.md")
    wiki_git.delete_path("Runbooks/only.md", "rm", author=None)
    assert _MisplacedPageDetector().validate(p) == (
        "the destination folder no longer holds any pages"
    )
