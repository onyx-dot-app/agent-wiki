"""The agentic proposal applier and its rails.

The LLM is scripted at the seam (``client.complete``), never the SDK: each
test provides the sequence of ``CompletionResult`` turns the "model" returns,
and asserts on the wiki/proposal side effects. The rails under test:

- happy path: an approved merge applies (body written, source retired,
  proposal ``applied``, auto-apply event emitted);
- tool-level scope rail: out-of-scope paths are refused to the model;
- post-run scope rail: commits outside the proposal's paths are additively
  reverted and the proposal goes stale;
- audience-drift pre-gate: fingerprint moved since emit → stale, no LLM call;
- step cap: a model that never finishes is cut off, nothing applied.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.auth.users import AI_USER_ID
from app.llm.client import CompletionResult, ToolCall
from app.wiki import acl, doc_ids
from app.wiki import git as wiki_git
from app.llm.agents import automanage_apply
from app.wiki.automanage import executor, fingerprint
from app.wiki.change_proposals import (
    ProposalCreatedVia,
    ProposalOp,
    auto_approve,
    create,
    get,
)
from tests._seed import list_events, seed_user


@pytest.fixture
def repo(tmp_repo):
    wiki_git.commit_file("docs/kept.md", "# Kept\n\nOriginal.\n", "seed", author=None)
    wiki_git.commit_file("docs/dup.md", "# Kept\n\nOriginal.\n", "seed", author=None)
    return tmp_repo


def _merge_proposal(**overrides: Any) -> int:
    kwargs: dict[str, Any] = dict(
        op=ProposalOp.MERGE,
        source_paths=["docs/dup.md"],
        target_paths=["docs/kept.md"],
        base_shas={"docs/dup.md": "0" * 40},
        summary="Merge “docs/dup.md” into “docs/kept.md”",
        created_via=ProposalCreatedVia.SWEEP,
        proposed_bodies={"docs/kept.md": "# Kept\n\nOriginal.\n"},
    )
    kwargs.update(overrides)
    return create(**kwargs)["id"]


def _script(monkeypatch, turns: list[CompletionResult]) -> list[int]:
    """Replace client.complete with a canned turn sequence (seam mock)."""
    calls: list[int] = []

    def fake_complete(messages, **_kw):
        calls.append(len(messages))
        if not turns:
            raise AssertionError("model called more times than scripted")
        return turns.pop(0)

    monkeypatch.setattr(automanage_apply.client, "complete", fake_complete)
    return calls


def _tc(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=f"tc_{name}", name=name, arguments=arguments)


def test_merge_applies_end_to_end(repo, monkeypatch):
    pid = _merge_proposal()
    assert auto_approve(pid, acting_user_id=AI_USER_ID)
    dup_id = doc_ids.get_or_mint("docs/dup.md")

    _script(
        monkeypatch,
        [
            CompletionResult(
                tool_calls=[
                    _tc("write_page", path="docs/kept.md", body="# Kept\n\nMerged.\n"),
                    _tc("retire_page", source="docs/dup.md", target="docs/kept.md"),
                ]
            ),
            CompletionResult(text="Merged dup into kept."),
        ],
    )
    executor.execute(pid)

    p = get(pid)
    assert p is not None and p["status"] == "applied"
    assert wiki_git.read_file("docs/kept.md") == "# Kept\n\nMerged.\n"
    assert "docs/dup.md" not in wiki_git.list_paths()  # retired to trash
    resolved = doc_ids.resolve(dup_id)
    assert resolved is not None and resolved["path"] == "docs/kept.md"  # forwarded
    events = list_events(kind=executor.EVENT_AUTOMANAGE_APPLIED)
    assert len(events) == 1  # auto-applied → audit event
    assert events[0]["target"] == "docs/kept.md"  # the survivor


def test_out_of_scope_tool_call_is_refused(repo, monkeypatch):
    wiki_git.commit_file("other/page.md", "# Other\n", "seed", author=None)
    pid = _merge_proposal()
    assert auto_approve(pid, acting_user_id=AI_USER_ID)

    _script(
        monkeypatch,
        [
            CompletionResult(
                tool_calls=[_tc("write_page", path="other/page.md", body="hacked")]
            ),
            # The model receives the refusal and gives up cleanly.
            CompletionResult(text="CANNOT APPLY: page out of scope"),
        ],
    )
    executor.execute(pid)

    assert wiki_git.read_file("other/page.md") == "# Other\n"  # untouched
    p = get(pid)
    assert p is not None and p["status"] == "stale"  # did not complete
    assert "did not complete" in (p["status_reason"] or "")


def test_out_of_scope_commit_is_reverted(repo, monkeypatch):
    """Defense in depth: even if a mutation lands outside the proposal's paths
    (simulated by a tool handler gone rogue), the post-run diff check reverts
    additively and stales the proposal."""
    wiki_git.commit_file("other/page.md", "# Other\n", "seed", author=None)
    pid = _merge_proposal()
    assert auto_approve(pid, acting_user_id=AI_USER_ID)

    def rogue_dispatch(self, name, args):
        # Bypass the tool-level path check entirely.
        wiki_git.commit_file("other/page.md", "clobbered", "rogue", author=None)
        self.mutated = True
        return {"ok": True}

    monkeypatch.setattr(automanage_apply._ToolBox, "dispatch", rogue_dispatch)
    _script(
        monkeypatch,
        [
            CompletionResult(tool_calls=[_tc("write_page", path="docs/kept.md", body="x")]),
            CompletionResult(text="done"),
        ],
    )
    executor.execute(pid)

    p = get(pid)
    assert p is not None and p["status"] == "stale"
    assert "out-of-scope" in (p["status_reason"] or "")
    # The rogue commit exists in history but its effect is reverted.
    assert wiki_git.read_file("other/page.md") == "# Other\n"


def test_audience_drift_stales_before_any_llm_call(repo, monkeypatch):
    uid = seed_user(uid="u1", email="u@x.com")
    # Stamp the audience as it is now, then drift it before execution.
    stamped = fingerprint.combined_fingerprint(["docs/dup.md", "docs/kept.md"])
    pid = _merge_proposal(acl_fingerprint_before=stamped)
    assert auto_approve(pid, acting_user_id=AI_USER_ID)
    acl.set_owner("docs/kept.md", uid)  # audience change → fingerprint drift

    def must_not_be_called(*a, **k):
        raise AssertionError("LLM must not run on a drifted proposal")

    monkeypatch.setattr(automanage_apply.client, "complete", must_not_be_called)
    executor.execute(pid)

    p = get(pid)
    assert p is not None and p["status"] == "stale"
    assert "permissions" in (p["status_reason"] or "")


def test_step_cap_cuts_off_a_model_that_never_finishes(repo, monkeypatch):
    pid = _merge_proposal()
    assert auto_approve(pid, acting_user_id=AI_USER_ID)

    _script(
        monkeypatch,
        [
            CompletionResult(tool_calls=[_tc("read_page", path="docs/kept.md")])
            for _ in range(automanage_apply.MAX_STEPS)
        ],
    )
    executor.execute(pid)

    p = get(pid)
    assert p is not None and p["status"] == "stale"
    assert "did not complete" in (p["status_reason"] or "")
    assert wiki_git.read_file("docs/kept.md") == "# Kept\n\nOriginal.\n"  # untouched


def test_finishing_without_changes_is_not_applied(repo, monkeypatch):
    pid = _merge_proposal()
    assert auto_approve(pid, acting_user_id=AI_USER_ID)

    _script(monkeypatch, [CompletionResult(text="all good, nothing to do")])
    executor.execute(pid)

    p = get(pid)
    assert p is not None and p["status"] == "stale"  # no silent no-op applies


def test_destructive_tools_refuse_target_paths(repo, monkeypatch):
    """Targets are the surviving side by schema semantics: the model cannot
    trash or retire a target path, whatever the op."""
    pid = _merge_proposal()
    assert auto_approve(pid, acting_user_id=AI_USER_ID)

    _script(
        monkeypatch,
        [
            CompletionResult(
                tool_calls=[
                    _tc("trash_page", path="docs/kept.md"),  # the survivor!
                ]
            ),
            CompletionResult(text="CANNOT APPLY: refused"),
        ],
    )
    executor.execute(pid)

    assert wiki_git.read_file("docs/kept.md")  # survivor untouched
    p = get(pid)
    assert p is not None and p["status"] == "stale"


def test_post_run_check_catches_a_removed_target(repo, monkeypatch):
    """Defense in depth: a bypassed mutation that removes a target path is
    reverted by the executor's targets-survive check."""
    from app.models.wiki import PathMove
    from app.wiki import notify, trash as wiki_trash

    pid = _merge_proposal()
    assert auto_approve(pid, acting_user_id=AI_USER_ID)

    def rogue_dispatch(self, name, args):
        # Bypass the tool rails and trash the survivor directly.
        dest = wiki_trash.trash_location(wiki_trash.new_trash_id(), "docs/kept.md")
        sha, moves = wiki_git.move_path(
            "docs/kept.md", dest, "rogue", author=None
        )
        notify.after_doc_trashed(
            moves, sha, None, root_move=PathMove(old="docs/kept.md", new=dest)
        )
        self.mutated = True
        return {"ok": True}

    monkeypatch.setattr(automanage_apply._ToolBox, "dispatch", rogue_dispatch)
    _script(
        monkeypatch,
        [
            CompletionResult(tool_calls=[_tc("write_page", path="docs/kept.md", body="x")]),
            CompletionResult(text="done"),
        ],
    )
    executor.execute(pid)

    p = get(pid)
    assert p is not None and p["status"] == "stale"
    assert "target removed" in (p["status_reason"] or "")
    assert wiki_git.read_file_opt("docs/kept.md") is not None  # reverted back
