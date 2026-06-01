"""Comments repo (app/wiki/comments.py) — CRUD, threading, and the re-anchor
helpers the commit-time drift task relies on.

DB-backed (uses the per-test schema from the ``tmp_db`` fixture), mirroring
``test_acl.py``.
"""
from __future__ import annotations

import pytest

from app.wiki import comments
from tests._seed import seed_user

_DOC = "guides/setup.md"


def _seed_root(author: str, *, doc: str = _DOC, sha: str = "sha1") -> dict:
    return comments.create_thread(
        doc_path=doc,
        body="is this still accurate?",
        author_user_id=author,
        anchor_sha=sha,
        start_offset=10,
        end_offset=25,
        quoted_text="the old wording",
    )


def test_create_thread_is_its_own_root(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    root = _seed_root(alice)
    assert root["id"].startswith("cmt_")
    assert root["thread_root_id"] == root["id"]
    assert root["parent_id"] is None
    assert root["status"] == "open"
    assert root["scope"] == "inline"
    assert root["author_kind"] == "user"
    assert (root["start_offset"], root["end_offset"]) == (10, 25)
    assert comments.get(root["id"]) == root


def test_inline_thread_requires_anchor(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    with pytest.raises(ValueError):
        comments.create_thread(
            doc_path=_DOC,
            body="x",
            author_user_id=alice,
            anchor_sha=None,
            start_offset=None,
            end_offset=None,
            quoted_text=None,
        )


def test_invalid_scope_and_author_kind_rejected(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    with pytest.raises(ValueError):
        comments.create_thread(
            doc_path=_DOC, body="x", author_user_id=alice, anchor_sha="s",
            start_offset=0, end_offset=1, quoted_text="x", scope="bogus",
        )
    with pytest.raises(ValueError):
        comments.create_thread(
            doc_path=_DOC, body="x", author_user_id=alice, anchor_sha="s",
            start_offset=0, end_offset=1, quoted_text="x", author_kind="robot",
        )


def test_reply_inherits_thread_and_doc_leaves_anchor_null(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    bob = seed_user(uid="u_bob", email="bob@x.com")
    root = _seed_root(alice)

    reply = comments.add_reply(parent_id=root["id"], body="yes, just edited", author_user_id=bob)
    assert reply is not None
    assert reply["thread_root_id"] == root["id"]
    assert reply["parent_id"] == root["id"]
    assert reply["doc_path"] == _DOC
    assert reply["scope"] == "inline"
    assert reply["anchor_sha"] is None
    assert reply["start_offset"] is None

    thread = comments.list_thread(root["id"])
    assert [c["id"] for c in thread] == [root["id"], reply["id"]]


def test_reply_to_missing_parent_returns_none(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    assert comments.add_reply(parent_id="cmt_missing", body="x", author_user_id=alice) is None


def test_list_for_doc_returns_all_rows_oldest_first(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    r1 = _seed_root(alice)
    r2 = _seed_root(alice, sha="sha1")
    comments.add_reply(parent_id=r1["id"], body="reply", author_user_id=alice)

    rows = comments.list_for_doc(_DOC)
    assert len(rows) == 3
    # other docs aren't included
    assert comments.list_for_doc("other/page.md") == []
    # all belong to one of the two threads
    assert {r["thread_root_id"] for r in rows} == {r1["id"], r2["id"]}


def test_edit_body(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    root = _seed_root(alice)
    updated = comments.edit_body(root["id"], "actually never mind")
    assert updated is not None
    assert updated["body"] == "actually never mind"
    assert comments.edit_body("cmt_missing", "x") is None


def test_resolve_and_reopen_thread(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    bob = seed_user(uid="u_bob", email="bob@x.com")
    root = _seed_root(alice)

    resolved = comments.set_thread_status(root["id"], "resolved", resolved_by_user_id=bob)
    assert resolved is not None
    assert resolved["status"] == "resolved"
    assert resolved["resolved_by_user_id"] == bob
    assert resolved["resolved_at"] is not None

    reopened = comments.set_thread_status(root["id"], "open")
    assert reopened is not None
    assert reopened["status"] == "open"
    assert reopened["resolved_by_user_id"] is None
    assert reopened["resolved_at"] is None


def test_set_status_rejects_system_only_and_unknown(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    root = _seed_root(alice)
    for bad in ("orphaned", "bogus"):
        with pytest.raises(ValueError):
            comments.set_thread_status(root["id"], bad)


def test_delete_root_cascades_replies(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    root = _seed_root(alice)
    reply = comments.add_reply(parent_id=root["id"], body="r", author_user_id=alice)
    assert reply is not None

    assert comments.delete(root["id"]) is True
    assert comments.get(root["id"]) is None
    assert comments.get(reply["id"]) is None  # cascaded
    assert comments.delete("cmt_missing") is False


# --------------------------------------------------------------------------- #
# Re-anchor helpers                                                           #
# --------------------------------------------------------------------------- #


def test_roots_needing_remap_filters(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    root = _seed_root(alice, sha="sha1")
    reply = comments.add_reply(parent_id=root["id"], body="r", author_user_id=alice)
    assert reply is not None

    # Already at head -> nothing to do.
    assert comments.roots_needing_remap(_DOC, "sha1") == []
    # Head advanced -> the root needs remapping; the reply is excluded.
    need = comments.roots_needing_remap(_DOC, "sha2")
    assert [c["id"] for c in need] == [root["id"]]

    # Orphaned roots are excluded; resolved roots are still remapped.
    comments.orphan(root["id"])
    assert comments.roots_needing_remap(_DOC, "sha2") == []


def test_apply_remap_advances_anchor_without_bumping_updated_at(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    root = _seed_root(alice, sha="sha1")
    before = comments.get(root["id"])
    assert before is not None

    comments.apply_remap(
        root["id"], start_offset=3, end_offset=9, quoted_text="new text", anchor_sha="sha2"
    )
    after = comments.get(root["id"])
    assert after is not None
    assert (after["start_offset"], after["end_offset"]) == (3, 9)
    assert after["quoted_text"] == "new text"
    assert after["anchor_sha"] == "sha2"
    assert after["updated_at"] == before["updated_at"]  # re-anchor is not user activity


def test_orphan_freezes_anchor_as_tombstone(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    root = _seed_root(alice, sha="sha1")
    comments.orphan(root["id"])
    after = comments.get(root["id"])
    assert after is not None
    assert after["status"] == "orphaned"
    # Offsets + quoted_text left intact as the tombstone.
    assert (after["start_offset"], after["end_offset"]) == (10, 25)
    assert after["quoted_text"] == "the old wording"
    assert after["anchor_sha"] == "sha1"


def test_reassign_doc_path_on_move(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    root = _seed_root(alice)
    comments.add_reply(parent_id=root["id"], body="r", author_user_id=alice)

    moved = comments.reassign_doc_path(_DOC, "guides/install.md")
    assert moved == 2  # root + reply
    assert comments.list_for_doc(_DOC) == []
    assert len(comments.list_for_doc("guides/install.md")) == 2


def test_orphan_all_for_doc_on_delete(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    r1 = _seed_root(alice, sha="sha1")
    r2 = _seed_root(alice, sha="sha1")
    comments.orphan(r1["id"])  # already orphaned -> not counted again

    orphaned = comments.orphan_all_for_doc(_DOC)
    assert orphaned == 1  # only r2 was still anchored
    assert comments.get(r2["id"])["status"] == "orphaned"
