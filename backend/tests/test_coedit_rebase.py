"""Live-rebase: folding an inbound agent commit into an open co-edit session
(app/wiki/coedit_rebase.py), plus the rebase_onto repo primitive it uses.
DB + real git (tmp_repo).
"""
from __future__ import annotations

import pytest

from app.auth import users as users_repo
from app.models.wiki import ChangeKind
from app.tasks import coedit_rebase as coedit_rebase_task
from app.tasks.queues import documents_queue, lightweight_maintenance_queue
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


# --- rebase_onto -------------------------------------------------------- #


def test_rebase_onto_folds_without_logging_a_session_op(tmp_db):
    # An agent's change reconciles into the buffer but is NOT a co-edit op — it
    # never enters coedit_ops / the session op stream.
    s = coedit.open_session(_PATH, base_sha="sha0", initial_buffer="hello world")
    res = coedit.rebase_onto(
        s.id, base_version=0, merged_text="HELLO world", new_base_sha="sha1", checkpointed=False,
    )
    assert res is not None
    assert res.changed is True
    assert res.session.version == 1
    assert res.session.buffer_text == "HELLO world" and res.session.base_sha == "sha1"
    assert coedit.ops_since_with_head(s.id, 0).ops == []  # no op logged for the fold


def test_rebase_onto_no_change_advances_base_only(tmp_db):
    s = coedit.open_session(_PATH, base_sha="sha0", initial_buffer="hi")
    res = coedit.rebase_onto(
        s.id, base_version=0, merged_text="hi", new_base_sha="sha1", checkpointed=False,
    )
    assert res is not None
    assert res.changed is False and res.session.version == 0 and res.session.base_sha == "sha1"


def test_rebase_onto_stale_version_returns_none(tmp_db):
    seed_uid = users_repo.create(email="a@x.com", password="hunter2-x", name="A")
    s = coedit.open_session(_PATH, base_sha="sha0", initial_buffer="hi")
    coedit.apply_op(s.id, base_version=0, changes=[_ch(0, 2, "yo")], author_user_id=seed_uid)
    # Caller still on version 0 — the CAS misses.
    res = coedit.rebase_onto(
        s.id, base_version=0, merged_text="X", new_base_sha="sha1", checkpointed=False,
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

    assert coedit_rebase.rebase_session(sess.id, new_sha) == coedit_rebase.RebaseOutcome.APPLIED

    st = coedit.get_session(sess.id)
    assert st is not None
    # Both edits now live in the buffer, which sits on top of the agent's HEAD.
    assert st.buffer_text == "ONE\ntwo\nthree\nfour\nFIVE\n"
    assert st.base_sha == new_sha
    assert st.version == 2


def test_rebase_skips_when_already_based_on_head(repo):
    sha = _seed("x\n")
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer="x\n")
    assert coedit_rebase.rebase_session(sess.id, sha) == coedit_rebase.RebaseOutcome.SKIP


def test_rebase_skips_stale_ancestor_head(repo):
    # A stale task carrying an older head_sha than the session's (already
    # advanced) base_sha must not "rebase backwards" and revert committed edits.
    old_sha = _seed("one\ntwo\n")
    sess = coedit.open_session(_PATH, base_sha=old_sha, initial_buffer="one\ntwo\n")
    # base_sha advances to a descendant (e.g. a checkpoint committed ONE).
    new_sha = wiki_git.commit_file(_PATH, "ONE\ntwo\n", "checkpoint", author="A <a@x.com>")
    coedit.rebase_onto(
        sess.id, base_version=0, merged_text="ONE\ntwo\n", new_base_sha=new_sha, checkpointed=True
    )
    # The late task still carries old_sha (an ancestor of the current base_sha).
    assert coedit_rebase.rebase_session(sess.id, old_sha) == coedit_rebase.RebaseOutcome.SKIP
    # Buffer untouched — the human's committed "ONE" edit is not reverted.
    st = coedit.get_session(sess.id)
    assert st is not None and st.buffer_text == "ONE\ntwo\n" and st.base_sha == new_sha


def _seed_conflict(uid) -> tuple[int, str]:
    # Human and agent both edit the first line → overlap. Returns (session_id, agent_sha).
    doc = "one\ntwo\n"
    sha = _seed(doc)
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer=doc)
    coedit.join(sess.id, uid)
    coedit.apply_op(sess.id, base_version=0, changes=[_ch(0, 3, "ONE")], author_user_id=uid)
    new_sha = wiki_git.commit_file(_PATH, "XXX\ntwo\n", "agent edit", author="Agent <a@x.com>")
    return sess.id, new_sha


def test_rebase_session_reports_conflict_and_leaves_buffer(repo):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sid, new_sha = _seed_conflict(uid)
    assert coedit_rebase.rebase_session(sid, new_sha) == coedit_rebase.RebaseOutcome.CONFLICT
    # Overlap is deferred to the checkpoint — the buffer is left as the human had it.
    st = coedit.get_session(sid)
    assert st is not None and st.buffer_text == "ONE\ntwo\n"


def test_conflict_hands_off_to_checkpoint_by_name(repo, monkeypatch):
    # The task (not the engine) enqueues the checkpoint on conflict, by name, so
    # no import of app.tasks.coedit_checkpoint is needed (would be circular).
    calls: list[int] = []
    monkeypatch.setitem(
        documents_queue.handlers, "checkpoint_coedit_session", lambda sid: calls.append(sid)
    )
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sid, new_sha = _seed_conflict(uid)

    with lightweight_maintenance_queue.immediate_mode(), documents_queue.immediate_mode():
        coedit_rebase_task.rebase_coedit_session(sid, new_sha)

    assert calls == [sid]


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
    monkeypatch.setattr(coedit, "rebase_onto", lambda *a, **k: None)
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed("a\nb\n")
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer="a\nb\n")
    coedit.join(sess.id, uid)
    new_sha = wiki_git.commit_file(_PATH, "a\nB\n", "agent edit", author="Agent <a@x.com>")
    assert coedit_rebase.rebase_session(sess.id, new_sha) == coedit_rebase.RebaseOutcome.RACED
