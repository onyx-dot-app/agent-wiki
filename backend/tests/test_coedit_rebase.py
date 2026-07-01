"""Live-rebase: folding an inbound agent commit into an open co-edit session
(app/wiki/coedit_rebase.py), plus the repo primitives it uses
(_diff_to_change, reconcile_onto). DB + real git (tmp_repo).
"""
from __future__ import annotations

import pytest

from app.auth import users as users_repo
from app.models.wiki import ChangeKind
from app.tasks.queues import lightweight_maintenance_queue
from app.wiki import coedit, coedit_checkpoint, coedit_rebase
from app.wiki import git as wiki_git
from app.wiki.utils import commit_and_fan_out

_PATH = "guides/setup.md"


def _seed(body: str) -> str:
    return wiki_git.commit_file(_PATH, body, "seed", author="Seed <seed@x.com>")


def _ch(frm: int, to: int, insert: str) -> coedit.Change:
    return coedit.Change.model_validate({"from": frm, "to": to, "insert": insert})


@pytest.fixture
def repo(tmp_repo):
    return tmp_repo


# --- _diff_to_change (pure, UTF-16) ---------------------------------------- #


def test_diff_to_change_identical_is_none():
    assert coedit._diff_to_change("abc", "abc") is None


def test_diff_to_change_replaces_middle():
    c = coedit._diff_to_change("hello world", "hello brave world")
    assert c is not None
    assert (c.from_, c.to, c.insert) == (6, 6, "brave ")
    assert coedit._apply_changes("hello world", [c]) == "hello brave world"


def test_diff_to_change_utf16_offsets_with_emoji():
    old, new = "a😀b", "a😀XYZb"  # emoji = 2 UTF-16 units; insert before trailing b
    c = coedit._diff_to_change(old, new)
    assert c is not None
    assert (c.from_, c.to, c.insert) == (3, 3, "XYZ")
    assert coedit._apply_changes(old, [c]) == new


# --- reconcile_onto -------------------------------------------------------- #


def test_reconcile_onto_folds_and_logs_system_op(tmp_db):
    users_repo.create(email="a@x.com", password="hunter2-x", name="A")
    s = coedit.open_session(_PATH, base_sha="sha0", initial_buffer="hello world")
    res = coedit.reconcile_onto(
        s.id, base_version=0, old_buffer="hello world",
        merged_text="HELLO world", new_base_sha="sha1", checkpointed=False,
    )
    assert res is not None
    row, change = res
    assert row.version == 1 and row.buffer_text == "HELLO world" and row.base_sha == "sha1"
    assert change is not None
    ops = coedit.ops_since(s.id, 0)
    assert len(ops) == 1 and ops[0].author_user_id is None  # system/merge-origin


def test_reconcile_onto_no_change_advances_base_only(tmp_db):
    s = coedit.open_session(_PATH, base_sha="sha0", initial_buffer="hi")
    res = coedit.reconcile_onto(
        s.id, base_version=0, old_buffer="hi",
        merged_text="hi", new_base_sha="sha1", checkpointed=False,
    )
    assert res is not None
    row, change = res
    assert change is None and row.version == 0 and row.base_sha == "sha1"
    assert coedit.ops_since(s.id, 0) == []  # nothing logged


def test_reconcile_onto_stale_version_returns_none(tmp_db):
    seed_uid = users_repo.create(email="a@x.com", password="hunter2-x", name="A")
    s = coedit.open_session(_PATH, base_sha="sha0", initial_buffer="hi")
    coedit.apply_op(s.id, base_version=0, changes=[_ch(0, 2, "yo")], author_user_id=seed_uid)
    # Caller still on version 0 — the CAS misses.
    res = coedit.reconcile_onto(
        s.id, base_version=0, old_buffer="hi",
        merged_text="X", new_base_sha="sha1", checkpointed=False,
    )
    assert res is None


# --- rebase_session (engine) ----------------------------------------------- #


def test_rebase_folds_clean_agent_commit(repo):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    doc = "one\ntwo\nthree\nfour\nfive\n"
    sha = _seed(doc)
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer=doc)
    coedit.join(sess.id, uid)
    # Human edits the first line in the live buffer...
    coedit.apply_op(sess.id, base_version=0, changes=[_ch(0, 3, "ONE")], author_user_id=uid)
    # ...an agent commits a distant (non-overlapping) change out of band.
    new_sha = wiki_git.commit_file(
        _PATH, "one\ntwo\nthree\nfour\nFIVE\n", "agent edit", author="Agent <a@x.com>"
    )

    assert coedit_rebase.rebase_session(sess.id, new_sha, "Agent") == "applied"

    st = coedit.get_session(sess.id)
    assert st is not None
    # Both edits now live in the buffer, which sits on top of the agent's HEAD.
    assert st.buffer_text == "ONE\ntwo\nthree\nfour\nFIVE\n"
    assert st.base_sha == new_sha
    assert st.version == 2


def test_rebase_skips_when_already_based_on_head(repo):
    sha = _seed("x\n")
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer="x\n")
    assert coedit_rebase.rebase_session(sess.id, sha, None) == "skip"


def test_rebase_conflict_enqueues_checkpoint(repo, monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(
        "app.tasks.coedit_checkpoint.checkpoint_coedit_session", lambda sid: calls.append(sid)
    )
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    doc = "one\ntwo\n"
    sha = _seed(doc)
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer=doc)
    coedit.join(sess.id, uid)
    # Human and agent both edit the first line → overlap.
    coedit.apply_op(sess.id, base_version=0, changes=[_ch(0, 3, "ONE")], author_user_id=uid)
    new_sha = wiki_git.commit_file(_PATH, "XXX\ntwo\n", "agent edit", author="Agent <a@x.com>")

    assert coedit_rebase.rebase_session(sess.id, new_sha, "Agent") == "conflict"
    assert calls == [sess.id]  # handed to the checkpoint's AI-merge, not folded


def test_agent_commit_through_gateway_triggers_live_rebase(repo):
    # End-to-end wiring: a commit through commit_and_fan_out → after_doc_write →
    # on_wiki_commit enqueues the rebase, which (in immediate mode) folds live.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    doc = "one\ntwo\nthree\nfour\nfive\n"
    sha = _seed(doc)
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer=doc)
    coedit.join(sess.id, uid)
    coedit.apply_op(sess.id, base_version=0, changes=[_ch(0, 3, "ONE")], author_user_id=uid)

    with lightweight_maintenance_queue.immediate_mode():
        commit_and_fan_out(
            _PATH, "one\ntwo\nthree\nfour\nFIVE\n", "agent edit",
            change_kind=ChangeKind.EDIT, base_body=doc, skip_acl=True, record_activity=False,
        )

    st = coedit.get_session(sess.id)
    assert st is not None
    assert st.buffer_text == "ONE\ntwo\nthree\nfour\nFIVE\n"


def test_checkpoint_commit_does_not_self_trigger_rebase(repo, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr("app.tasks.coedit_rebase.on_wiki_commit", lambda *a, **k: calls.append(a))
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer="hello world")
    coedit.join(sess.id, uid)
    coedit.apply_op(sess.id, base_version=0, changes=[_ch(0, 5, "hi")], author_user_id=uid)

    coedit_checkpoint.checkpoint_session(sess.id)

    # The checkpoint passes trigger_coedit_rebase=False — its own commit must not
    # be folded back into the session as an inbound rebase.
    assert calls == []


def test_rebase_raced_op_is_skipped(repo, monkeypatch):
    # A human op landing mid-merge makes the CAS miss; we skip (checkpoint backstop).
    monkeypatch.setattr(coedit, "reconcile_onto", lambda *a, **k: None)
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed("a\nb\n")
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer="a\nb\n")
    coedit.join(sess.id, uid)
    new_sha = wiki_git.commit_file(_PATH, "a\nB\n", "agent edit", author="Agent <a@x.com>")
    assert coedit_rebase.rebase_session(sess.id, new_sha, "Agent") == "raced"
