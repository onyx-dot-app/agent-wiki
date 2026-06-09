"""The `search_comments` chat tool handler (app/llm/agents/tools/search_comments.py).

Pure handler logic — `comment_fts.search` and `current_user` are patched, so
these run without OpenSearch. ACL scoping and indexing are covered by
`test_comment_search.py` and `test_agent_tool_permissions.py`.
"""
from __future__ import annotations

from app.db.comment_fts import CommentSearchHit
from app.llm.agents.tools import search_comments as tool


def test_empty_query_errors():
    assert tool.handle({"query": "   "}) == {"error": "query is required"}
    assert tool.handle({}) == {"error": "query is required"}


def test_no_hits_returns_note(monkeypatch):
    monkeypatch.setattr(tool, "current_user", lambda: None)
    monkeypatch.setattr(tool.comment_fts, "search", lambda *a, **k: [])
    assert tool.handle({"query": "nothing"}) == {
        "results": [],
        "note": "no matching comments",
    }


def test_shapes_results_with_deep_link(monkeypatch):
    hit = CommentSearchHit(
        comment_id="cmt_reply",
        doc_path="oncall runbook.md",
        thread_root_id="cmt_root",
        snippet="biweekly",
        score=1.5,
    )
    monkeypatch.setattr(tool, "current_user", lambda: None)
    monkeypatch.setattr(tool.comment_fts, "search", lambda *a, **k: [hit])

    out = tool.handle({"query": "biweekly"})
    assert len(out["results"]) == 1
    r = out["results"][0]
    assert r["doc_path"] == "oncall runbook.md"
    # Link uses the thread root (not the matched reply) and url-encodes the path.
    assert r["thread_root_id"] == "cmt_root"
    assert r["link"] == "/app/wiki/oncall%20runbook.md?comment=cmt_root"
    assert r["snippet"] == "biweekly"


def test_limit_is_clamped(monkeypatch):
    seen: dict[str, int] = {}

    def fake_search(query: str, limit: int = 10, **kwargs: object):
        seen["limit"] = limit
        return []

    monkeypatch.setattr(tool, "current_user", lambda: None)
    monkeypatch.setattr(tool.comment_fts, "search", fake_search)

    tool.handle({"query": "x", "limit": 999})
    assert seen["limit"] == tool.MAX_LIMIT
    tool.handle({"query": "x", "limit": 0})
    assert seen["limit"] == 1
