"""Template-echo detector — untouched template instances propose removal.

Pure blob-sha matching against the current template bodies, an age grace
window, a validate premise (still matches *some* current template), and the
precedence rule: body-dup skips duplicate groups that are template echoes.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.llm.agents import automanage_apply
from app.llm.client import CompletionResult, ToolCall
from app.tasks.queues import automanage_nearline_queue
from app.wiki import git as wiki_git
from app.wiki import templates
from app.wiki.automanage import review, runner
from app.wiki.automanage.detectors.base import Scope, TriggerKind
from app.wiki.automanage.detectors.body_dup import _BodyDupDetector
from app.wiki.automanage.detectors.template_echo import _TemplateEchoDetector
from app.wiki.change_proposals import (
    ProposalStatus,
    list_by_status,
)
from app.wiki.change_proposals import (
    get as get_proposal,
)
from tests._seed import seed_user

_TPL = "# Weekly Notes\n\n## Highlights\n\n## Blockers\n\n## Next week\n" + "x" * 100


@pytest.fixture
def repo(tmp_repo, tmp_config):
    templates.create(
        name="Weekly Notes",
        body=_TPL,
        description=None,
        system_prompt=None,
        created_by_user_id=None,
    )
    return tmp_config


def _scope(*paths: str) -> Scope:
    return Scope(trigger=TriggerKind.SWEEP, paths=tuple(paths))


def test_untouched_template_instance_is_proposed_for_removal(repo):
    wiki_git.commit_file("notes/week-30.md", _TPL, "from template", author=None)

    drafts = _TemplateEchoDetector(min_age_days=0).detect(_scope("notes/week-30.md"))

    assert len(drafts) == 1
    d = drafts[0]
    assert d.op.value == "delete_page"
    assert d.source_paths == ["notes/week-30.md"]
    assert d.target_paths == []
    assert "Weekly Notes" in d.summary
    assert d.auto_approvable is False
    assert d.instruction and "trash_page" in d.instruction


def test_fresh_instantiation_is_left_alone(repo):
    # Same echo, but inside the grace window (default 7 days).
    wiki_git.commit_file("notes/week-31.md", _TPL, "from template", author=None)
    assert _TemplateEchoDetector().detect(_scope("notes/week-31.md")) == []


def test_filled_in_page_is_not_an_echo(repo):
    wiki_git.commit_file(
        "notes/week-32.md", _TPL + "\nActual content this week.\n", "filled", author=None
    )
    assert _TemplateEchoDetector(min_age_days=0).detect(_scope("notes/week-32.md")) == []


def test_validate_tracks_the_premise(repo):
    wiki_git.commit_file("notes/week-33.md", _TPL, "from template", author=None)
    det = _TemplateEchoDetector(min_age_days=0)
    (draft,) = det.detect(_scope("notes/week-33.md"))
    proposal: dict[str, Any] = {"source_paths": draft.source_paths}

    assert det.validate(proposal) is None  # still an echo

    wiki_git.commit_file("notes/week-33.md", _TPL + "\nEdited.\n", "edit", author=None)
    reason = det.validate(proposal)
    assert reason is not None and "no longer matches" in reason


def test_body_dup_skips_echo_groups(repo):
    # Two untouched instances of the same template: byte-identical, but they
    # are echoes — removal territory, not merge territory.
    wiki_git.commit_file("a/notes.md", _TPL, "t", author=None)
    wiki_git.commit_file("b/notes.md", _TPL, "t", author=None)

    assert _BodyDupDetector().detect(_scope("a/notes.md", "b/notes.md")) == []


def test_executes_end_to_end_with_trash_page(repo, monkeypatch):
    monkeypatch.setattr(
        runner, "DETECTORS", [_TemplateEchoDetector(min_age_days=0)]
    )
    wiki_git.commit_file("notes/week-34.md", _TPL, "from template", author=None)
    runner.run_sweep(triggered_by_user_id=None)
    pending = list_by_status(ProposalStatus.PENDING)
    assert len(pending) == 1
    pid = pending[0]["id"]
    assert pending[0]["detector"] == "template_echo"

    turns = [
        CompletionResult(
            tool_calls=[
                ToolCall(id="t1", name="trash_page", arguments={"path": "notes/week-34.md"})
            ]
        ),
        CompletionResult(text="Removed the untouched template page."),
    ]
    monkeypatch.setattr(
        automanage_apply.client, "complete", lambda messages, **kw: turns.pop(0)
    )
    uid = seed_user(uid="rv", email="rv@x.com")
    with automanage_nearline_queue.immediate_mode():
        review.approve(pid, user_id=uid)

    p = get_proposal(pid)
    assert p is not None and p["status"] == "applied"
    assert "notes/week-34.md" not in wiki_git.list_paths()  # in trash
    assert any(
        t.endswith("/notes/week-34.md") for t in wiki_git.list_trash_files()
    )
