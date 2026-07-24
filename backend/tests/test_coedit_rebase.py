"""Live-rebase: reacting to an inbound agent/ingest commit landing under an
open co-edit session (app/wiki/coedit_rebase.py + app/tasks/coedit_rebase.py).

Since a Yjs checkpoint can only run in the process holding the room, this
path doesn't fold the external commit itself — it decides whether one is
needed (``rebase_session``) and, if so, signals the owning process via the
realtime bus rather than trying to reconcile inline. See the module
docstrings and ``Engineering Projects/Agent Wiki Project/design/Co-Editing.md``.
"""
from __future__ import annotations

from app.tasks import coedit_rebase as coedit_rebase_task
from app.wiki import coedit, coedit_rebase
from app.wiki import git as wiki_git
from app.wiki.utils import commit_and_fan_out
from app.models.wiki import ChangeKind

_PATH = "guides/setup.md"


def _seed(body: str) -> str:
    return wiki_git.commit_file(_PATH, body, "seed", author="Seed <seed@x.com>")


# --- rebase_session (engine) ------------------------------------------- #


def test_rebase_needs_checkpoint_for_external_commit(tmp_repo):
    sha = _seed("one\ntwo\n")
    sess = coedit.open_session(_PATH, base_sha=sha)
    new_sha = wiki_git.commit_file(_PATH, "one\nTWO\n", "agent edit", author="Agent <a@x.com>")

    assert (
        coedit_rebase.rebase_session(sess.id, new_sha)
        == coedit_rebase.RebaseOutcome.NEEDS_CHECKPOINT
    )


def test_rebase_skips_when_already_based_on_head(tmp_repo):
    sha = _seed("x\n")
    sess = coedit.open_session(_PATH, base_sha=sha)
    assert coedit_rebase.rebase_session(sess.id, sha) == coedit_rebase.RebaseOutcome.SKIP


def test_rebase_skips_gone_session(tmp_repo):
    sha = _seed("x\n")
    assert coedit_rebase.rebase_session(999999, sha) == coedit_rebase.RebaseOutcome.SKIP


def test_rebase_skips_closed_session(tmp_repo):
    sha = _seed("x\n")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.close_session(sess.id)
    new_sha = wiki_git.commit_file(_PATH, "y\n", "agent edit", author="Agent <a@x.com>")
    assert coedit_rebase.rebase_session(sess.id, new_sha) == coedit_rebase.RebaseOutcome.SKIP


def test_rebase_skips_stale_ancestor_head(tmp_repo):
    # A stale task carrying an older head_sha than the session's (already
    # advanced) base_sha must not "rebase backwards".
    old_sha = _seed("one\ntwo\n")
    sess = coedit.open_session(_PATH, base_sha=old_sha)
    new_sha = wiki_git.commit_file(_PATH, "ONE\ntwo\n", "checkpoint", author="A <a@x.com>")
    coedit.checkpoint_ydoc(sess.id, snapshot=b"snap", base_sha=new_sha, seq=1)

    assert coedit_rebase.rebase_session(sess.id, old_sha) == coedit_rebase.RebaseOutcome.SKIP
    st = coedit.get_ydoc_state(sess.id)
    assert st is not None and st.base_sha == new_sha  # untouched


# --- task: bus signal ---------------------------------------------------- #


def test_task_emits_bus_signal_on_needs_checkpoint(tmp_repo, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(coedit_rebase_task.bus, "emit", lambda payload: calls.append(payload))
    sha = _seed("one\ntwo\n")
    sess = coedit.open_session(_PATH, base_sha=sha)
    new_sha = wiki_git.commit_file(_PATH, "one\nTWO\n", "agent edit", author="Agent <a@x.com>")

    coedit_rebase_task.rebase_coedit_session(sess.id, new_sha, _PATH)

    assert calls == [{"kind": "coedit_checkpoint_needed", "path": _PATH}]


def test_task_does_not_emit_when_skipped(tmp_repo, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(coedit_rebase_task.bus, "emit", lambda payload: calls.append(payload))
    sha = _seed("x\n")
    sess = coedit.open_session(_PATH, base_sha=sha)

    coedit_rebase_task.rebase_coedit_session(sess.id, sha, _PATH)

    assert calls == []


def test_on_wiki_commit_triggers_rebase_for_external_commit(tmp_repo, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        coedit_rebase_task, "rebase_coedit_session", lambda *a: calls.append(a)
    )
    sha = _seed("x\n")
    coedit.open_session(_PATH, base_sha=sha)
    new_sha = wiki_git.commit_file(_PATH, "y\n", "agent edit", author="Agent <a@x.com>")

    coedit_rebase_task.on_wiki_commit(_PATH, new_sha)

    assert len(calls) == 1
    _sid, head_sha, path = calls[0]
    assert (head_sha, path) == (new_sha, _PATH)


def test_on_wiki_commit_skips_the_sessions_own_checkpoint_commit(tmp_repo, monkeypatch):
    # A checkpoint's own commit sets base_sha == the new sha before
    # on_wiki_commit fires — must not treat its own commit as "external".
    calls: list[tuple] = []
    monkeypatch.setattr(
        coedit_rebase_task, "rebase_coedit_session", lambda *a: calls.append(a)
    )
    sha = _seed("x\n")
    sess = coedit.open_session(_PATH, base_sha=sha)
    new_sha = wiki_git.commit_file(_PATH, "y\n", "checkpoint", author="Ada <a@x.com>")
    coedit.checkpoint_ydoc(sess.id, snapshot=b"snap", base_sha=new_sha, seq=1)

    coedit_rebase_task.on_wiki_commit(_PATH, new_sha)

    assert calls == []


def test_agent_commit_through_gateway_triggers_live_rebase_signal(tmp_repo, monkeypatch):
    # End-to-end wiring: a commit through commit_and_fan_out → after_doc_write
    # → on_wiki_commit → rebase_coedit_session → bus.emit.
    calls: list[dict] = []
    monkeypatch.setattr(coedit_rebase_task.bus, "emit", lambda payload: calls.append(payload))
    doc = "one\ntwo\n"
    sha = _seed(doc)
    coedit.open_session(_PATH, base_sha=sha)

    with coedit_rebase_task.lightweight_maintenance_queue.immediate_mode():
        commit_and_fan_out(
            _PATH, "one\nTWO\n", "agent edit",
            change_kind=ChangeKind.EDIT, base_body=doc, skip_acl=True, record_activity=False,
        )

    assert calls == [{"kind": "coedit_checkpoint_needed", "path": _PATH}]
