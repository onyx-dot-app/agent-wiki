"""Live-rebase: folding an out-of-band commit into an open co-edit session
(app/wiki/coedit_rebase.py), plus the trigger (app/tasks/coedit_rebase.py).
DB + real git (tmp_repo).

No rooms, and none of the guards a room needed. The fold is an ordinary logged
Yjs update built from ``(ydoc_snapshot, coedit_updates)``, so:

* any process can do it — there is no "not my session, skip" case, and
  ``rebase_session`` is plain sync rather than async;
* the update commutes with concurrent keystrokes, so there is no ``RACED``
  outcome, no generation check and no ``expected_seq`` compare-and-swap;
* clients receive the fold as normal traffic instead of a resync.

What's left to assert is narrower and more behavioural: the merged text lands in
the session, it's *logged* (so a peer that missed the broadcast still converges,
and a later checkpoint replays it), ``base_sha`` advances, and an overlap defers
to the checkpoint engine.
"""
from __future__ import annotations

import pytest
from pycrdt import Doc, XmlFragment

from app.auth import users as users_repo
from app.tasks import coedit_rebase as coedit_rebase_task
from app.tasks.queues import coedit_queue
from app.wiki import coedit, coedit_live, coedit_rebase
from app.wiki import git as wiki_git
from app.wiki.markdown_yjs import ROOT_XML_KEY, reconstruct_body, seed_doc_from_markdown

_PATH = "guides/setup.md"


def _seed(body: str) -> str:
    return wiki_git.commit_file(_PATH, body, "seed", author="Seed <seed@x.com>")


@pytest.fixture
def repo(tmp_repo):
    return tmp_repo


def _root(doc):
    # No return-type annotation — see test_coedit_checkpoint.py's _root.
    return doc.get(ROOT_XML_KEY, type=XmlFragment)


def _session(body: str, base_sha: str) -> int:
    """An active session with the initial snapshot every rebuild starts from."""
    sess = coedit.open_session(_PATH, base_sha=base_sha)
    coedit.set_initial_snapshot(sess.id, seed_doc_from_markdown(body).get_update(), body)
    return sess.id


def _edit(session_id: int, uid: str, prefix: str) -> None:
    """Type ``prefix`` at the head of the first block, the way a client would:
    rebuild from the log, mutate, log the delta. Deliberately a *delta*
    (``get_update(before)``), not the whole doc state — that's what a client
    sends, and replaying whole-state payloads would hide a broken delta."""
    doc, _seq = _rebuild(session_id)
    before = doc.get_state()
    with doc.transaction():
        _root(doc).children[0].children[0].insert(0, prefix)
    coedit.apply_update(session_id, update_bytes=doc.get_update(before), author_user_id=uid)


def _rebuild(session_id: int):
    """The document as any process would reconstruct it: snapshot + every
    logged update. Mirrors coedit_live._load, which can't hand a Doc back
    across a thread boundary (PyO3 unsendable), so tests rebuild their own."""
    sess = coedit.get_session_for_checkpoint(session_id)
    assert sess is not None and sess.ydoc_snapshot is not None
    doc = Doc()
    doc.apply_update(sess.ydoc_snapshot)
    since = coedit.updates_since(session_id, sess.ydoc_snapshot_seq)
    for u in since.updates:
        doc.apply_update(u.update_payload)
    return doc, since.head_seq


# --- rebase_session (engine) ----------------------------------------------- #


def test_rebase_folds_clean_agent_commit(repo):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    body = "one\ntwo\nthree\nfour\nfive\n"
    sha = _seed(body)
    sid = _session(body, sha)
    coedit.join(sid, uid)
    # A human edits the first line; an agent commits a distant, non-overlapping
    # change out of band.
    _edit(sid, uid, "EDIT-")
    new_sha = wiki_git.commit_file(
        _PATH, "one\ntwo\nthree\nfour\nFIVE\n", "agent edit", author="Agent <a@x.com>"
    )

    assert coedit_rebase.rebase_session(sid, new_sha) == coedit_rebase.RebaseOutcome.APPLIED

    # Both edits are in the document, and it sits on the agent's HEAD.
    doc, head_seq = _rebuild(sid)
    assert reconstruct_body(doc) == "EDIT-one\ntwo\nthree\nfour\nFIVE\n"
    st = coedit.get_session(sid)
    assert st is not None and st.base_sha == new_sha
    # Logged as an update, not applied out of band: seq 1 is the human's edit,
    # seq 2 the fold. That's what lets a peer who missed the broadcast catch up
    # via get_updates_since, and a later checkpoint replay it.
    assert head_seq == 2
    assert st.ydoc_seq == 2


def test_fold_is_logged_with_no_human_author(repo):
    # The fold has no author — attributing it to whoever last typed would
    # credit them with an agent's edit, and coedit_updates.author_user_id is
    # nullable precisely for this.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    body = "one\ntwo\nthree\n"
    sha = _seed(body)
    sid = _session(body, sha)
    _edit(sid, uid, "EDIT-")
    new_sha = wiki_git.commit_file(
        _PATH, "one\ntwo\nTHREE\n", "agent edit", author="Agent <a@x.com>"
    )

    coedit_rebase.rebase_session(sid, new_sha)

    authors = [u.author_user_id for u in coedit.updates_since(sid, 0).updates]
    assert authors == [uid, None]
    # A checkpoint still gets attributed to the human, not to nobody.
    assert coedit.last_update_author(sid) == uid


def test_rebase_skips_when_already_based_on_head(repo):
    sha = _seed("x\n")
    sid = _session("x\n", sha)
    assert coedit_rebase.rebase_session(sid, sha) == coedit_rebase.RebaseOutcome.SKIP


def test_rebase_skips_a_closed_session(repo):
    sha = _seed("one\ntwo\n")
    sid = _session("one\ntwo\n", sha)
    coedit.close_session(sid)
    new_sha = wiki_git.commit_file(_PATH, "one\nTWO\n", "agent", author="A <a@x.com>")
    assert coedit_rebase.rebase_session(sid, new_sha) == coedit_rebase.RebaseOutcome.SKIP


def test_rebase_skips_stale_ancestor_head(repo):
    # A stale trigger can carry an older head_sha than the session's (already
    # advanced) base_sha. Merging against that older content would diff
    # backwards and revert committed edits, so it must be skipped.
    old_sha = _seed("one\ntwo\n")
    sid = _session("one\ntwo\n", old_sha)
    # base_sha advances to a descendant (e.g. a checkpoint committed ONE).
    new_sha = wiki_git.commit_file(_PATH, "ONE\ntwo\n", "checkpoint", author="A <a@x.com>")
    coedit.set_base_sha(sid, new_sha)

    outcome = coedit_rebase.rebase_session(sid, old_sha)

    assert outcome == coedit_rebase.RebaseOutcome.SKIP
    st = coedit.get_session(sid)
    assert st is not None and st.base_sha == new_sha
    assert st.ydoc_seq == 0  # nothing logged — the document was never touched


def _seed_conflict(uid: str) -> tuple[int, str]:
    """Human and agent both rewrite the first line → overlap.
    Returns (session_id, agent_sha)."""
    body = "one\ntwo\n"
    sha = _seed(body)
    sid = _session(body, sha)
    coedit.join(sid, uid)
    _edit(sid, uid, "ONE-")
    new_sha = wiki_git.commit_file(_PATH, "XXX\ntwo\n", "agent edit", author="Agent <a@x.com>")
    return sid, new_sha


def test_rebase_reports_conflict_and_leaves_the_document_alone(repo):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sid, new_sha = _seed_conflict(uid)

    outcome = coedit_rebase.rebase_session(sid, new_sha)

    assert outcome == coedit_rebase.RebaseOutcome.CONFLICT
    # Untouched — the checkpoint engine's AI merge resolves this, not us, so
    # neither the document nor the merge base may move here.
    doc, _seq = _rebuild(sid)
    assert reconstruct_body(doc) == "ONE-one\ntwo\n"
    st = coedit.get_session(sid)
    assert st is not None and st.base_sha != new_sha


def test_rebase_noop_advances_base_without_logging_an_update(repo):
    # The commit, once merged, is exactly what the document already said. Only
    # the merge base moves, so the next checkpoint diffs against the right
    # commit.
    #
    # The agent's commit has to be a genuinely new one: committing
    # byte-identical content is a no-op to git.commit_file, which then returns
    # the existing HEAD sha, collapsing new_sha to base_sha and tripping the
    # already-based-on-head skip before the merge runs at all. So the human and
    # the agent make the *same* edit independently.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed("same\n")
    sid = _session("same\n", sha)
    coedit.join(sid, uid)
    _edit(sid, uid, "AGENT-")
    new_sha = wiki_git.commit_file(
        _PATH, "AGENT-same\n", "same edit, committed", author="A <a@x.com>"
    )

    outcome = coedit_rebase.rebase_session(sid, new_sha)

    assert outcome == coedit_rebase.RebaseOutcome.NOOP
    st = coedit.get_session(sid)
    assert st is not None
    assert st.base_sha == new_sha
    assert st.ydoc_seq == 1  # the human's edit only; the fold logged nothing


def test_fold_survives_a_concurrent_edit(repo, monkeypatch):
    # The property the delta buys us, and the reason the old CAS/generation
    # guards are gone: an edit that lands after the merge has read the document
    # is not lost, because both are just updates on the same log.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    body = "one\ntwo\nthree\nfour\nfive\n"
    sha = _seed(body)
    sid = _session(body, sha)
    coedit.join(sid, uid)
    new_sha = wiki_git.commit_file(
        _PATH, "one\ntwo\nthree\nfour\nFIVE\n", "agent edit", author="Agent <a@x.com>"
    )

    real_merge_content = coedit_live.merge_content

    def racing_merge_content(*args, **kwargs):
        # A keystroke landing after the fold has read the document and before it
        # logs its delta — the exact interleaving that used to force a RACED
        # skip. Patched on coedit_live, which binds the name at import.
        _edit(sid, uid, "LATE-")
        return real_merge_content(*args, **kwargs)

    monkeypatch.setattr(coedit_live, "merge_content", racing_merge_content)
    outcome = coedit_rebase.rebase_session(sid, new_sha)

    assert outcome == coedit_rebase.RebaseOutcome.APPLIED
    # Neither edit lost: the late keystroke and the agent's line are both there.
    updates = coedit.updates_since(sid, 0).updates
    assert [u.author_user_id for u in updates] == [uid, None]  # keystroke, then fold
    doc, _seq = _rebuild(sid)
    assert reconstruct_body(doc) == "LATE-one\ntwo\nthree\nfour\nFIVE\n"
    st = coedit.get_session(sid)
    assert st is not None and st.base_sha == new_sha


# --- the trigger (app/tasks/coedit_rebase.py) ------------------------------ #


def test_on_wiki_commit_enqueues_for_an_active_session(repo, monkeypatch):
    body = "one\ntwo\n"
    sha = _seed(body)
    sid = _session(body, sha)
    new_sha = wiki_git.commit_file(_PATH, "one\nTWO\n", "agent", author="A <a@x.com>")
    calls: list[tuple[int, str]] = []
    monkeypatch.setattr(
        coedit_rebase_task,
        "rebase_coedit_session",
        lambda session_id, head_sha: calls.append((session_id, head_sha)),
    )

    coedit_rebase_task.on_wiki_commit(_PATH, new_sha)

    assert calls == [(sid, new_sha)]


def test_on_wiki_commit_skips_when_no_active_session(repo, monkeypatch):
    calls: list[tuple[int, str]] = []
    monkeypatch.setattr(
        coedit_rebase_task,
        "rebase_coedit_session",
        lambda session_id, head_sha: calls.append((session_id, head_sha)),
    )
    coedit_rebase_task.on_wiki_commit(_PATH, _seed("x\n"))
    assert calls == []


def test_on_wiki_commit_skips_the_sessions_own_checkpoint_commit(repo, monkeypatch):
    # base_sha already equals sha — the session's own checkpoint commit landing
    # as the after_doc_write callback that fired this. Must not fold a session's
    # own save back into itself.
    sha = _seed("x\n")
    _session("x\n", sha)
    calls: list[tuple[int, str]] = []
    monkeypatch.setattr(
        coedit_rebase_task,
        "rebase_coedit_session",
        lambda session_id, head_sha: calls.append((session_id, head_sha)),
    )
    coedit_rebase_task.on_wiki_commit(_PATH, sha)
    assert calls == []


def test_conflict_falls_back_to_the_checkpoint_engine(repo, monkeypatch):
    # The task, not the engine, hands a CONFLICT to the checkpoint engine —
    # enqueued rather than run inline so a long LLM merge doesn't hold this
    # task's co-edit queue slot. Stubbed, so this checks the hand-off, not the
    # merge (that's the checkpoint engine's own tests).
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sid, new_sha = _seed_conflict(uid)
    enqueued: list[int] = []
    monkeypatch.setattr(
        coedit_rebase_task, "checkpoint_coedit_session_task", enqueued.append
    )

    with coedit_queue.immediate_mode():
        coedit_rebase_task.rebase_coedit_session(sid, new_sha)

    assert enqueued == [sid]


def test_applied_rebase_does_not_fall_back_to_the_checkpoint_engine(repo, monkeypatch):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    body = "one\ntwo\nthree\nfour\nfive\n"
    sha = _seed(body)
    sid = _session(body, sha)
    coedit.join(sid, uid)
    _edit(sid, uid, "EDIT-")
    new_sha = wiki_git.commit_file(
        _PATH, "one\ntwo\nthree\nfour\nFIVE\n", "agent edit", author="Agent <a@x.com>"
    )
    enqueued: list[int] = []
    monkeypatch.setattr(
        coedit_rebase_task, "checkpoint_coedit_session_task", enqueued.append
    )

    with coedit_queue.immediate_mode():
        coedit_rebase_task.rebase_coedit_session(sid, new_sha)

    assert enqueued == []  # clean merge — no fallback needed


def test_live_read_reflects_a_folded_commit(repo):
    # What a page read returns while a session is open. Any process can serve
    # it now, so this is also the check that the fold is durable rather than
    # resident in whichever worker did it.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    body = "one\ntwo\nthree\n"
    sha = _seed(body)
    sid = _session(body, sha)
    _edit(sid, uid, "EDIT-")
    new_sha = wiki_git.commit_file(
        _PATH, "one\ntwo\nTHREE\n", "agent edit", author="Agent <a@x.com>"
    )

    coedit_rebase.rebase_session(sid, new_sha)

    assert coedit_live.read_body(sid) == "EDIT-one\ntwo\nTHREE\n"
