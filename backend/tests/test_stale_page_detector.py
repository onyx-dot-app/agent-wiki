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
from tests._seed import seed_user

_OLD_BODY = "# 2025 Offsite Agenda\n\nSessions for the June 2025 offsite.\n"
_LIVE_BODY = "# Runbook\n\nSteps that stay current.\n"


def _backdated_commit(path: str, body: str, days: int = 60) -> None:
    """Commit ``path`` with author+committer dates ``days`` ago — the only
    way to seed genuinely old pages (the prefilter reads real git dates).
    Test seeding only, same precedent as ``plumb_commit``."""
    import os
    import subprocess

    import app.config

    cwd = app.config.CONFIG.wiki_dir
    when = (datetime.now(UTC) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )
    absd = os.path.join(cwd, os.path.dirname(path))
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
    _backdated_commit("notes/old.md", _OLD_BODY)
    page_views.touch("notes/old.md")  # a fresh view
    calls = _script(monkeypatch)
    det = _StalePageDetector()
    assert det.detect(_scope("notes/old.md")) == []
    assert calls == []


def test_young_tracking_gates_unviewed_pages(monkeypatch, tmp_repo):
    """With no recorded views at all, 'no view row' proves nothing — the
    detector must stay silent rather than call everything unviewed."""
    _backdated_commit("notes/old.md", _OLD_BODY)
    calls = _script(monkeypatch)
    det = _StalePageDetector()
    assert det.detect(_scope("notes/old.md")) == []
    assert calls == []


# ---- agent pass ------------------------------------------------------------ #


def test_confirmed_stale_page_emits_a_deletion_draft(monkeypatch, tmp_repo):
    _age_tracking()
    _backdated_commit("notes/old.md", _OLD_BODY)
    _script(
        monkeypatch,
        _finish({"path": "notes/old.md", "evidence": "agenda for a past event"}),
    )

    (draft,) = _StalePageDetector().detect(
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
    _backdated_commit("notes/old.md", _OLD_BODY)
    _script(
        monkeypatch,
        _finish({"path": "made/up.md", "evidence": "not a candidate"}),
    )
    det = _StalePageDetector()
    drafts = det.detect(_scope("notes/old.md"))
    assert drafts == []


def test_cap_bounds_proposals_per_sweep(monkeypatch, tmp_repo):
    _age_tracking()
    items = []
    paths = [f"notes/old-{i}.md" for i in range(5)]
    for p in paths:
        _backdated_commit(p, _OLD_BODY + p)
        items.append({"path": p, "evidence": "past event"})
    _script(monkeypatch, _finish(*items))
    drafts = _StalePageDetector().detect(_scope(*paths, "anchor/tracked.md"))
    assert len(drafts) == 3  # MAX_PROPOSALS


def test_llm_failure_degrades_to_empty(monkeypatch, tmp_repo):
    _age_tracking()
    _backdated_commit("notes/old.md", _OLD_BODY)

    def boom(messages, **kwargs):
        raise LLMError("not_configured", "LLM is not configured")

    monkeypatch.setattr(llm_client, "complete", boom)
    assert _StalePageDetector().detect(_scope("notes/old.md")) == []


def test_agent_can_read_candidates_and_search(monkeypatch, tmp_repo):
    """One investigate turn (read + search) before finish — the tool results
    ride back into the transcript."""
    _age_tracking()
    _backdated_commit("notes/old.md", _OLD_BODY)
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
        "app.wiki.automanage.detectors.llm_agent.fts.search", lambda *a, **k: []
    )

    (draft,) = _StalePageDetector().detect(_scope("notes/old.md"))

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


def test_search_never_surfaces_cross_audience_pages(monkeypatch, tmp_repo):
    """The coverage search must not leak a restricted page's path/title into
    the transcript: hits outside the bucket's audience are dropped."""
    from types import SimpleNamespace

    from app.wiki import acl
    from app.wiki.automanage import fingerprint
    from app.wiki.automanage.detectors import llm_agent

    wiki_git.commit_file("public/page.md", _OLD_BODY, "seed", author=None)
    wiki_git.commit_file("secret/page.md", _OLD_BODY + "s", "seed", author=None)
    owner = seed_user(uid="owner", email="o@x.com")
    acl.set_owner("secret/page.md", owner)  # restricted: different audience

    hits = [
        SimpleNamespace(path="public/page.md", title="Public"),
        SimpleNamespace(path="secret/page.md", title="Secret"),
    ]
    monkeypatch.setattr(llm_agent.fts, "search", lambda *a, **k: hits)

    fp = fingerprint.fingerprints_for_paths(["public/page.md"])["public/page.md"]
    out = llm_agent.dispatch_tool(
        "stale_page", "search_wiki", {"query": "q"}, {"public/page.md"}, fp
    )

    assert out == [{"path": "public/page.md", "title": "Public"}]
