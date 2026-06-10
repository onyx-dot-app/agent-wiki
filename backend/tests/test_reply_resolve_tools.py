"""reply_comment + resolve_comment chat tools.

DB-backed (`tmp_db`); `current_user` patched to a seeded user (the FK on
`author_user_id` / `resolved_by_user_id` needs a real row). The page is
implicit-public (no ACL rows), so `require_can("read", ...)` passes.
"""
from __future__ import annotations

from app.auth import User
from app.llm.agents.tools.reply_comment import handle as reply_handle
from app.llm.agents.tools.resolve_comment import handle as resolve_handle
from app.wiki import comments
from tests._seed import seed_user

_DOC = "guides/setup.md"


def _root(author: str) -> dict:
    return comments.create_thread(
        doc_path=_DOC,
        body="root question",
        author_user_id=author,
        anchor_sha="sha1",
        start_offset=0,
        end_offset=4,
        quoted_text="root",
    )


def _as_user(monkeypatch) -> str:
    uid = seed_user(uid="u_a", email="a@x.com")
    user = User(id=uid, email="a@x.com", name=None, is_admin=False)
    monkeypatch.setattr("app.auth.current_user", lambda: user)
    monkeypatch.setattr("app.llm.agents.tools.reply_comment.current_user", lambda: user)
    monkeypatch.setattr("app.llm.agents.tools.resolve_comment.current_user", lambda: user)
    return uid


# --- reply_comment -------------------------------------------------------- #


def test_reply_attaches_to_thread(tmp_db, monkeypatch):
    uid = _as_user(monkeypatch)
    root = _root(uid)

    out = reply_handle({"comment_id": root["id"], "body": "here's my answer"})
    assert out["thread_root_id"] == root["id"]
    assert out["doc_path"] == _DOC
    assert out["link"] == f"/app/wiki/guides/setup.md?comment={root['id']}"

    thread = comments.list_thread(root["id"])
    assert len(thread) == 2
    reply = next(c for c in thread if c["parent_id"])
    assert reply["author_kind"] == "agent"
    assert reply["author_user_id"] == uid
    assert reply["body"] == "here's my answer"


def test_reply_unknown_comment_errors(tmp_db, monkeypatch):
    _as_user(monkeypatch)
    out = reply_handle({"comment_id": "cmt_nope", "body": "x"})
    assert "error" in out and "not found" in out["error"]


def test_reply_requires_body_and_id(tmp_db, monkeypatch):
    uid = _as_user(monkeypatch)
    root = _root(uid)
    assert "error" in reply_handle({"comment_id": root["id"], "body": "   "})
    assert "error" in reply_handle({"comment_id": "  ", "body": "hi"})


# --- resolve_comment ------------------------------------------------------ #


def test_resolve_marks_thread_resolved(tmp_db, monkeypatch):
    uid = _as_user(monkeypatch)
    root = _root(uid)

    out = resolve_handle({"comment_id": root["id"]})
    assert out["status"] == "resolved"
    assert out["thread_root_id"] == root["id"]
    refreshed = comments.get(root["id"])
    assert refreshed is not None and refreshed["status"] == "resolved"


def test_resolve_via_reply_id_resolves_the_thread(tmp_db, monkeypatch):
    uid = _as_user(monkeypatch)
    root = _root(uid)
    reply = comments.add_reply(parent_id=root["id"], body="r", author_user_id=uid)
    assert reply is not None

    out = resolve_handle({"comment_id": reply["id"]})
    assert out["thread_root_id"] == root["id"]
    refreshed = comments.get(root["id"])
    assert refreshed is not None and refreshed["status"] == "resolved"


def test_resolve_unknown_comment_errors(tmp_db, monkeypatch):
    _as_user(monkeypatch)
    out = resolve_handle({"comment_id": "cmt_nope"})
    assert "error" in out and "not found" in out["error"]
