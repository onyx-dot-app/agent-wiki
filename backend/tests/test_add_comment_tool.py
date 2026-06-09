"""The `add_comment` chat tool (app/llm/agents/tools/add_comment.py).

Real wiki repo (`tmp_repo`) so the snippet anchoring runs against a real
committed body, and a real DB so the agent-authored thread is created. Mirrors
`test_comments_api.py`. `current_user` is patched to a seeded admin (the page is
implicit-public anyway; this keeps `require_can` deterministic).
"""
from __future__ import annotations

from app.auth import User
from app.llm.agents.tools.add_comment import handle
from app.wiki import comments, git as wiki_git

_PAGE = "guides/db.md"
_BODY = "Intro line.\nThe connection pool size is 20.\nClosing line.\n"
_SNIPPET = "connection pool size is 20"


def _commit(body: str = _BODY) -> None:
    wiki_git.commit_file(_PAGE, body, "seed", author=None)


def _as_user(monkeypatch) -> None:
    user = User(id="u_a", email="a@x.com", name=None, is_admin=True)
    monkeypatch.setattr("app.auth.current_user", lambda: user)


def test_add_comment_anchors_to_snippet(tmp_repo, monkeypatch):
    _commit()
    _as_user(monkeypatch)

    out = handle({"path": _PAGE, "quoted_text": _SNIPPET, "body": "still accurate?"})
    assert out["doc_path"] == _PAGE
    assert out["comment_id"].startswith("cmt_")
    assert out["link"] == f"/app/wiki/guides/db.md?comment={out['comment_id']}"

    rows = comments.list_for_doc(_PAGE)
    assert len(rows) == 1
    c = rows[0]
    assert c["author_kind"] == "agent"
    assert c["author_user_id"] is None
    assert c["quoted_text"] == _SNIPPET
    start = _BODY.index(_SNIPPET)
    assert (c["start_offset"], c["end_offset"]) == (start, start + len(_SNIPPET))


def test_rejects_missing_snippet(tmp_repo, monkeypatch):
    _commit()
    _as_user(monkeypatch)
    out = handle({"path": _PAGE, "quoted_text": "not on the page", "body": "x"})
    assert "error" in out and "not found" in out["error"]
    assert comments.list_for_doc(_PAGE) == []


def test_rejects_ambiguous_snippet(tmp_repo, monkeypatch):
    _commit("the same line\nthe same line\n")
    _as_user(monkeypatch)
    out = handle({"path": _PAGE, "quoted_text": "the same line", "body": "x"})
    assert "error" in out and "more than once" in out["error"]
    assert comments.list_for_doc(_PAGE) == []


def test_requires_body_and_quote(tmp_repo, monkeypatch):
    _commit()
    _as_user(monkeypatch)
    assert "error" in handle({"path": _PAGE, "quoted_text": _SNIPPET, "body": "   "})
    assert "error" in handle({"path": _PAGE, "quoted_text": "  ", "body": "hi"})


def test_file_not_found(tmp_repo, monkeypatch):
    _as_user(monkeypatch)
    out = handle({"path": "no/such.md", "quoted_text": "x", "body": "hi"})
    assert "error" in out and "not found" in out["error"]
