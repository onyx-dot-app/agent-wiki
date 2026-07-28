"""Live-rebase: folding an inbound agent commit into an open co-edit session
(app/wiki/coedit_rebase.py), plus the trigger + cross-process fan-out
(app/tasks/coedit_rebase.py). DB + real git (tmp_repo) + an in-process room
(coedit_room) — rebase_session only ever acts on a session whose room lives
in this process (see coedit_room.py).

``rebase_session`` is ``async`` — it touches a live room's ``Doc`` directly
(unlike ``checkpoint_session``, which no longer does at all; see
test_coedit_checkpoint.py's module docstring) and must stay on this
process's own thread to do so — driven here via ``asyncio.run``.
"""
from __future__ import annotations

import asyncio

import pytest
from pycrdt import XmlFragment

from app.auth import users as users_repo
from app.models.wiki import ChangeKind
from app.tasks import coedit_rebase as coedit_rebase_task
from app.wiki import coedit, coedit_checkpoint, coedit_rebase, coedit_room
from app.wiki import git as wiki_git
from app.wiki.markdown_yjs import ROOT_XML_KEY, reconstruct_body, seed_doc_from_markdown
from app.wiki.utils import commit_and_fan_out

_PATH = "guides/setup.md"


def _seed(body: str) -> str:
    return wiki_git.commit_file(_PATH, body, "seed", author="Seed <seed@x.com>")


@pytest.fixture
def repo(tmp_repo):
    return tmp_repo


def _root(doc):
    # No return-type annotation — see test_coedit_checkpoint.py's _root for
    # why (matches the established test_markdown_splice.py precedent).
    return doc.get(ROOT_XML_KEY, type=XmlFragment)


def _room(sess, body: str) -> coedit_room.Room:
    # Includes the initial snapshot (coedit.set_initial_snapshot) even
    # though rebase_session itself never reads it — only the checkpoint
    # engine does, but this file's helpers are shared with the one test
    # here that falls through to a real checkpoint
    # (test_checkpoint_commit_does_not_self_trigger_rebase), which needs
    # it; harmless overhead for the rebase-only tests. See
    # test_coedit_checkpoint.py's _room for the same pattern.
    room = coedit_room.create_room(sess.id, sess.path, body, sess.base_sha)
    coedit.set_initial_snapshot(sess.id, room.doc.get_update())
    return room


def _edit(sess, room, uid: str, prefix: str) -> None:
    """Logs the full post-edit Doc state as the update payload, not a
    placeholder — real, applicable Yjs bytes, since a real checkpoint
    (see above) now replays them. See test_coedit_checkpoint.py's _edit."""
    root = _root(room.doc)
    with room.doc.transaction():
        root.children[0].children[0].insert(0, prefix)
    coedit.apply_update(sess.id, update_bytes=room.doc.get_update(), author_user_id=uid)


def _run(coro):
    return asyncio.run(coro)


# --- rebase_session (engine) ----------------------------------------------- #


def test_rebase_folds_clean_agent_commit(repo):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    doc = "one\ntwo\nthree\nfour\nfive\n"
    sha = _seed(doc)
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    room = _room(sess, doc)
    # Human edits inside the live doc; an agent commits a distant
    # (non-overlapping) change out of band.
    _edit(sess, room, uid, "EDIT-")
    new_sha = wiki_git.commit_file(
        _PATH, "one\ntwo\nthree\nfour\nFIVE\n", "agent edit", author="Agent <a@x.com>"
    )

    outcome = _run(coedit_rebase.rebase_session(sess.id, new_sha))
    assert outcome == coedit_rebase.RebaseOutcome.APPLIED

    st = coedit.get_session(sess.id)
    assert st is not None
    # Both edits now live in the doc, which sits on top of the agent's HEAD.
    live = coedit_room.get_room(sess.id)
    assert live is not None
    assert reconstruct_body(live.doc) == "EDIT-one\ntwo\nthree\nfour\nFIVE\n"
    assert live.base_sha == new_sha
    assert st.base_sha == new_sha
    assert st.ydoc_seq == 2


def test_rebase_skips_when_no_local_room(repo):
    # A session with no room in this process — the state the periodic
    # process-locality guard exists for (see coedit_room.py). SKIP for this
    # reason takes priority over every other check.
    sha = _seed("x\n")
    sess = coedit.open_session(_PATH, base_sha=sha)
    outcome = _run(coedit_rebase.rebase_session(sess.id, "some-other-sha"))
    assert outcome == coedit_rebase.RebaseOutcome.SKIP


def test_rebase_skips_when_already_based_on_head(repo):
    sha = _seed("x\n")
    sess = coedit.open_session(_PATH, base_sha=sha)
    _room(sess, "x\n")
    assert _run(coedit_rebase.rebase_session(sess.id, sha)) == coedit_rebase.RebaseOutcome.SKIP


def test_rebase_skips_stale_ancestor_head(repo):
    # A stale trigger carrying an older head_sha than the session's (already
    # advanced) base_sha must not "rebase backwards" and revert committed
    # edits.
    old_sha = _seed("one\ntwo\n")
    sess = coedit.open_session(_PATH, base_sha=old_sha)
    room = _room(sess, "one\ntwo\n")
    # base_sha advances to a descendant (e.g. a checkpoint committed ONE).
    new_sha = wiki_git.commit_file(_PATH, "ONE\ntwo\n", "checkpoint", author="A <a@x.com>")
    coedit_room.reseed(room, "ONE\ntwo\n", new_sha)
    coedit.rebase_onto(
        sess.id,
        new_base_sha=new_sha,
        snapshot=seed_doc_from_markdown("ONE\ntwo\n").get_update(),
        checkpointed=True,
    )

    # The late trigger still carries old_sha (an ancestor of the current base_sha).
    outcome = _run(coedit_rebase.rebase_session(sess.id, old_sha))
    assert outcome == coedit_rebase.RebaseOutcome.SKIP
    # Doc untouched — the human's committed "ONE" edit is not reverted.
    st = coedit.get_session(sess.id)
    assert st is not None and st.base_sha == new_sha
    assert reconstruct_body(room.doc) == "ONE\ntwo\n"


def _seed_conflict(uid) -> tuple[int, str]:
    # Human and agent both edit the first line → overlap. Returns (session_id, agent_sha).
    doc = "one\ntwo\n"
    sha = _seed(doc)
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    room = _room(sess, doc)
    _edit(sess, room, uid, "ONE-")
    new_sha = wiki_git.commit_file(_PATH, "XXX\ntwo\n", "agent edit", author="Agent <a@x.com>")
    return sess.id, new_sha


def test_rebase_session_reports_conflict_and_leaves_doc(repo):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sid, new_sha = _seed_conflict(uid)
    outcome = _run(coedit_rebase.rebase_session(sid, new_sha))
    assert outcome == coedit_rebase.RebaseOutcome.CONFLICT
    # Overlap is deferred to the checkpoint — the doc is left as the human had it.
    live = coedit_room.get_room(sid)
    assert live is not None
    assert reconstruct_body(live.doc) == "ONE-one\ntwo\n"


def test_rebase_raced_session_is_skipped(repo, monkeypatch):
    # A concurrent close makes rebase_onto's conditional UPDATE miss; skip
    # (the checkpoint scan is the backstop for a genuinely stuck session).
    monkeypatch.setattr(coedit, "rebase_onto", lambda *a, **k: None)
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed("a\nb\n")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    _room(sess, "a\nb\n")
    new_sha = wiki_git.commit_file(_PATH, "a\nB\n", "agent edit", author="Agent <a@x.com>")
    outcome = _run(coedit_rebase.rebase_session(sess.id, new_sha))
    assert outcome == coedit_rebase.RebaseOutcome.RACED


def test_rebase_noop_when_merge_matches_live_doc(repo):
    # The external commit, once merged, produces exactly what the doc
    # already had (e.g. it's a no-op relative to the live edit) — base_sha
    # still advances, but nothing is reseeded/resynced.
    sha = _seed("same\n")
    sess = coedit.open_session(_PATH, base_sha=sha)
    _room(sess, "same\n")
    new_sha = wiki_git.commit_file(_PATH, "same\n", "no-op agent commit", author="A <a@x.com>")
    outcome = _run(coedit_rebase.rebase_session(sess.id, new_sha))
    assert outcome == coedit_rebase.RebaseOutcome.NOOP
    st = coedit.get_session(sess.id)
    assert st is not None and st.base_sha == new_sha


# --- trigger + cross-process fan-out (app/tasks/coedit_rebase.py) ---------- #


def test_conflict_falls_back_to_checkpoint_engine(repo, monkeypatch):
    # The trigger (not the engine) hands a CONFLICT off to the checkpoint
    # task — enqueued (via asyncio.to_thread, since the task itself is a
    # plain sync call now) rather than run inline, stubbed here so the test
    # verifies the hand-off, not a real LLM call (that's the checkpoint
    # engine's own concern).
    calls: list[int] = []

    def fake_checkpoint(session_id: int) -> None:
        calls.append(session_id)

    monkeypatch.setattr(coedit_rebase_task, "checkpoint_coedit_session_task", fake_checkpoint)
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sid, new_sha = _seed_conflict(uid)

    _run(coedit_rebase_task._rebase_and_maybe_checkpoint(sid, new_sha))

    assert calls == [sid]


def test_applied_rebase_does_not_fall_back_to_checkpoint(repo, monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(
        coedit_rebase_task, "checkpoint_coedit_session_task", lambda sid: calls.append(sid)
    )
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    doc = "one\ntwo\n"
    sha = _seed(doc)
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    room = _room(sess, doc)
    _edit(sess, room, uid, "EDIT-")
    new_sha = wiki_git.commit_file(_PATH, "one\nTWO\n", "agent edit", author="Agent <a@x.com>")

    _run(coedit_rebase_task._rebase_and_maybe_checkpoint(sess.id, new_sha))

    assert calls == []  # clean merge — no checkpoint fallback needed


def test_try_local_schedules_when_room_present(repo, monkeypatch):
    scheduled: list[tuple[int, str]] = []

    def fake_run_on_main_loop(coro) -> None:
        scheduled.append(coro)
        coro.close()  # never actually run — just proving it was scheduled

    monkeypatch.setattr(coedit_room, "run_on_main_loop", fake_run_on_main_loop)
    sha = _seed("x\n")
    sess = coedit.open_session(_PATH, base_sha=sha)
    _room(sess, "x\n")

    coedit_rebase_task._try_local(sess.id, "new-sha")
    assert len(scheduled) == 1


def test_try_local_noop_without_a_room(repo, monkeypatch):
    scheduled: list[object] = []
    monkeypatch.setattr(coedit_room, "run_on_main_loop", lambda coro: scheduled.append(coro))
    sha = _seed("x\n")
    sess = coedit.open_session(_PATH, base_sha=sha)
    # No room created for this session in this process.
    coedit_rebase_task._try_local(sess.id, "new-sha")
    assert scheduled == []


def test_on_wiki_commit_skips_when_no_active_session(repo, monkeypatch):
    calls: list[object] = []
    monkeypatch.setattr(coedit_rebase_task, "_try_local", lambda *a: calls.append(a))
    coedit_rebase_task.on_wiki_commit(_PATH, "some-sha")
    assert calls == []


def test_on_wiki_commit_skips_own_checkpoint_commit(repo, monkeypatch):
    # base_sha already equals sha — the session's own checkpoint commit
    # landing as the after_doc_write callback that fired this. Must not
    # trigger a rebase against itself.
    calls: list[object] = []
    monkeypatch.setattr(coedit_rebase_task, "_try_local", lambda *a: calls.append(a))
    sha = _seed("x\n")
    coedit.open_session(_PATH, base_sha=sha)
    coedit_rebase_task.on_wiki_commit(_PATH, sha)
    assert calls == []


def test_agent_commit_through_gateway_triggers_live_rebase(repo, monkeypatch):
    # End-to-end wiring: a commit through commit_and_fan_out -> after_doc_write
    # -> on_wiki_commit fans out over the bus and tries locally; since this
    # process holds the session's room, the rebase runs as a callback
    # scheduled onto (in this test) the loop the whole thing executes on.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    doc = "one\ntwo\nthree\nfour\nfive\n"
    sha = _seed(doc)
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    room = _room(sess, doc)
    _edit(sess, room, uid, "EDIT-")

    async def run():
        monkeypatch.setattr(coedit_room, "_main_loop", asyncio.get_running_loop())
        result = commit_and_fan_out(
            _PATH,
            "one\ntwo\nthree\nfour\nFIVE\n",
            "agent edit",
            change_kind=ChangeKind.EDIT,
            base_body=doc,
            skip_acl=True,
            record_activity=False,
        )
        assert result is not None
        # The rebase is scheduled as a fire-and-forget callback on this same
        # loop (run_coroutine_threadsafe) — poll briefly for it to land
        # rather than assume it's synchronous.
        for _ in range(50):
            live = coedit_room.get_room(sess.id)
            if live is not None and live.base_sha == result.sha:
                return result
            await asyncio.sleep(0.02)
        return result

    result = asyncio.run(run())
    live = coedit_room.get_room(sess.id)
    assert live is not None
    assert live.base_sha == result.sha
    assert reconstruct_body(live.doc) == "EDIT-one\ntwo\nthree\nfour\nFIVE\n"


def test_checkpoint_commit_does_not_self_trigger_rebase(repo, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(coedit_rebase_task, "on_wiki_commit", lambda *a, **k: calls.append(a))
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    room = _room(sess, "hello world")
    _edit(sess, room, uid, "hi ")

    coedit_checkpoint.checkpoint_session(sess.id)  # sync now — no _run wrapper needed

    # The checkpoint's commit passes trigger_coedit_rebase=False — its own
    # commit must not be folded back into the session as an inbound rebase.
    assert calls == []
