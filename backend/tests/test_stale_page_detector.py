"""Stale-page detector — the first LLM detector.

The mechanical prefilter (edit age + view age + tracking floor) gates what
ever reaches the model; the scripted LLM (patched at the ``client.complete``
seam, never the SDK) exercises the agent pass; validate() is the mechanical
activity check. Conservative rails: candidate-only proposals, per-sweep cap,
never auto-approvable, LLM failure degrades to an empty result.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import update as sa_update

from app.db.models import WikiDocId
from app.db.session import session

from app.llm import client as llm_client
from app.llm.client import CompletionResult, ToolCall, Usage
from app.llm.errors import LLMError
from app.wiki import doc_ids
from app.wiki import git as wiki_git
from app.wiki import page_views
from app.wiki.automanage.detectors.base import Scope, TriggerKind
from app.wiki.automanage.detectors.stale_page import _StalePageDetector
from app.wiki.change_proposals import ProposalOp

_OLD_BODY = "# 2025 Offsite Agenda\n\nSessions for the June 2025 offsite.\n"
_LIVE_BODY = "# Runbook\n\nSteps that stay current.\n"


@pytest.fixture(autouse=True)
def _fresh(tmp_repo):
    page_views.reset_for_tests()
    yield


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
    """Feed canned completions; record each call's messages."""
    calls: list[list[dict[str, Any]]] = []
    queue = list(results)

    def fake(messages, **kwargs):
        calls.append(messages)
        return queue.pop(0)

    monkeypatch.setattr(llm_client, "complete", fake)
    return calls


def _age_tracking() -> None:
    """Give view tracking an old floor so no-row pages count as unviewed."""
    wiki_git.commit_file("anchor/tracked.md", "# t\n", "seed", author=None)
    page_views.touch("anchor/tracked.md")
    old = (datetime.now(UTC) - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
    doc_id = doc_ids.id_for_path("anchor/tracked.md")
    with session() as s:
        s.execute(
            sa_update(WikiDocId)
            .where(WikiDocId.id == doc_id)
            .values(last_viewed_at=old)
        )


# ---- prefilter ------------------------------------------------------------ #


def test_recently_edited_pages_never_reach_the_llm(monkeypatch, tmp_repo):
    wiki_git.commit_file("notes/old.md", _OLD_BODY, "seed", author=None)
    calls = _script(monkeypatch)  # any LLM call would pop an empty queue
    det = _StalePageDetector()  # default floor: a just-committed page is fresh
    assert det.detect(_scope("notes/old.md")) == []
    assert calls == []


def test_recently_viewed_pages_never_reach_the_llm(monkeypatch, tmp_repo):
    _age_tracking()
    wiki_git.commit_file("notes/old.md", _OLD_BODY, "seed", author=None)
    page_views.touch("notes/old.md")  # a fresh view
    calls = _script(monkeypatch)
    det = _StalePageDetector(floor_days=0)
    assert det.detect(_scope("notes/old.md")) == []
    assert calls == []


def test_young_tracking_gates_unviewed_pages(monkeypatch, tmp_repo):
    """With no recorded views at all, 'no view row' proves nothing — the
    detector must stay silent rather than call everything unviewed."""
    wiki_git.commit_file("notes/old.md", _OLD_BODY, "seed", author=None)
    calls = _script(monkeypatch)
    det = _StalePageDetector(floor_days=0)
    assert det.detect(_scope("notes/old.md")) == []
    assert calls == []


# ---- agent pass ------------------------------------------------------------ #


def test_confirmed_stale_page_emits_a_deletion_draft(monkeypatch, tmp_repo):
    _age_tracking()
    wiki_git.commit_file("notes/old.md", _OLD_BODY, "seed", author=None)
    _script(
        monkeypatch,
        _finish({"path": "notes/old.md", "evidence": "agenda for a past event"}),
    )

    (draft,) = _StalePageDetector(floor_days=0).detect(
        _scope("notes/old.md", "anchor/tracked.md")
    )

    assert draft.op == ProposalOp.DELETE_PAGE
    assert draft.source_paths == ["notes/old.md"]
    assert "agenda for a past event" in draft.summary
    assert draft.auto_approvable is False  # deletions always get a human
    meta = wiki_git.last_commit_meta_for_path("notes/old.md")
    assert meta is not None and draft.premise == meta[0]


def test_non_candidate_proposals_are_dropped(monkeypatch, tmp_repo):
    _age_tracking()
    wiki_git.commit_file("notes/old.md", _OLD_BODY, "seed", author=None)
    _script(
        monkeypatch,
        _finish({"path": "made/up.md", "evidence": "not a candidate"}),
    )
    det = _StalePageDetector(floor_days=0)
    drafts = det.detect(_scope("notes/old.md"))
    assert drafts == []


def test_cap_bounds_proposals_per_sweep(monkeypatch, tmp_repo):
    _age_tracking()
    items = []
    paths = [f"notes/old-{i}.md" for i in range(5)]
    for p in paths:
        wiki_git.commit_file(p, _OLD_BODY + p, "seed", author=None)
        items.append({"path": p, "evidence": "past event"})
    _script(monkeypatch, _finish(*items))
    drafts = _StalePageDetector(floor_days=0).detect(_scope(*paths, "anchor/tracked.md"))
    assert len(drafts) == 3  # MAX_PROPOSALS


def test_llm_failure_degrades_to_empty(monkeypatch, tmp_repo):
    _age_tracking()
    wiki_git.commit_file("notes/old.md", _OLD_BODY, "seed", author=None)

    def boom(messages, **kwargs):
        raise LLMError("not_configured", "LLM is not configured")

    monkeypatch.setattr(llm_client, "complete", boom)
    assert _StalePageDetector(floor_days=0).detect(_scope("notes/old.md")) == []


def test_agent_can_read_candidates_and_search(monkeypatch, tmp_repo):
    """One investigate turn (read + search) before finish — the tool results
    ride back into the transcript."""
    _age_tracking()
    wiki_git.commit_file("notes/old.md", _OLD_BODY, "seed", author=None)
    investigate = CompletionResult(
        text="",
        tool_calls=[
            ToolCall(id="r1", name="read_page", arguments={"path": "notes/old.md"}),
            ToolCall(id="s1", name="search_wiki", arguments={"query": "offsite"}),
        ],
        stop_reason="tool_use",
        usage=Usage(),
    )
    calls = _script(
        monkeypatch,
        investigate,
        _finish({"path": "notes/old.md", "evidence": "agenda; covered elsewhere"}),
    )
    monkeypatch.setattr(
        "app.wiki.automanage.detectors.stale_page.fts.search", lambda *a, **k: []
    )

    (draft,) = _StalePageDetector(floor_days=0).detect(_scope("notes/old.md"))

    assert draft.source_paths == ["notes/old.md"]
    # Second LLM call saw the tool results.
    tool_msgs = [m for m in calls[1] if m.get("role") == "tool"]
    assert len(tool_msgs) == 2
    assert "2025 Offsite" in tool_msgs[0]["content"]


# ---- validate --------------------------------------------------------------- #


def _proposal_for(path: str) -> dict[str, Any]:
    meta = wiki_git.last_commit_meta_for_path(path)
    assert meta is not None
    return {
        "source_paths": [path],
        "base_shas": {path: meta[0]},
        "created_at": "2020-01-01 00:00:00",
        "last_emitted_at": "2020-01-01 00:00:00",
    }


def test_validate_holds_while_nothing_happened(tmp_repo):
    wiki_git.commit_file("notes/old.md", _OLD_BODY, "seed", author=None)
    p = _proposal_for("notes/old.md")
    assert _StalePageDetector().validate(p) is None


def test_validate_stales_on_edit(tmp_repo):
    wiki_git.commit_file("notes/old.md", _OLD_BODY, "seed", author=None)
    p = _proposal_for("notes/old.md")
    wiki_git.commit_file("notes/old.md", _OLD_BODY + "update\n", "edit", author=None)
    assert _StalePageDetector().validate(p) == (
        "the page changed since it was judged stale"
    )


def test_validate_stales_on_view(tmp_repo):
    wiki_git.commit_file("notes/old.md", _OLD_BODY, "seed", author=None)
    p = _proposal_for("notes/old.md")
    page_views.touch("notes/old.md")  # a view after the (backdated) judgment
    assert _StalePageDetector().validate(p) == (
        "the page was viewed since it was judged stale"
    )


def test_validate_stales_on_missing_page(tmp_repo):
    wiki_git.commit_file("notes/old.md", _OLD_BODY, "seed", author=None)
    p = _proposal_for("notes/old.md")
    wiki_git.delete_path("notes/old.md", "rm", author=None)
    assert "no longer exists" in (_StalePageDetector().validate(p) or "")
