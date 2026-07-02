"""Checkpoint engine (app/wiki/coedit_checkpoint.py) — commits a session's live
buffer to git via the merge gateway, with last-editor author + co-author
trailers. DB + real git (the ``tmp_repo`` fixture).
"""
from __future__ import annotations

import pytest

from app.auth import users as users_repo
from app.db.models import DocumentTemplate
from app.db.session import session as db_session
from app.tasks import coedit_checkpoint as coedit_checkpoint_task
from app.tasks.queues import documents_queue
from app.wiki import coedit, coedit_checkpoint, drafts
from app.wiki import git as wiki_git

_PATH = "guides/setup.md"


def _seed_page(body: str) -> str:
    return wiki_git.commit_file(_PATH, body, "seed", author="Seed <seed@x.com>")


def _ch(frm: int, to: int, insert: str) -> coedit.Change:
    return coedit.Change.model_validate({"from": frm, "to": to, "insert": insert})


@pytest.fixture
def repo(tmp_repo):
    return tmp_repo


def test_checkpoint_commits_buffer_and_attributes_last_editor(repo):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer="hello world")
    coedit.join(sess.id, uid)
    coedit.apply_op(sess.id, base_version=0, changes=[_ch(0, 5, "hi")], author_user_id=uid)

    new_sha = coedit_checkpoint.checkpoint_session(sess.id)

    assert new_sha is not None
    assert wiki_git.read_file(_PATH) == "hi world"
    st = coedit.get_active_session(_PATH)
    assert st is not None
    assert st.checkpointed_version == 1
    assert st.base_sha == new_sha
    # git author is the last editor.
    assert wiki_git.history(_PATH)[0].author == "Ada"


def test_checkpoint_syncs_merged_buffer_and_doesnt_drop_agent_edit(repo):
    # When the checkpoint's 3-way merge folds in a concurrent agent commit, the
    # merged result must be written back into the buffer — otherwise a later
    # checkpoint would re-commit the pre-merge buffer and silently drop the
    # agent's edit.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    doc = "one\ntwo\nthree\nfour\nfive\n"
    sha = _seed_page(doc)
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer=doc)
    coedit.join(sess.id, uid)
    # Human edits the first line in the buffer; agent commits a distant change.
    coedit.apply_op(sess.id, base_version=0, changes=[_ch(0, 3, "ONE")], author_user_id=uid)
    wiki_git.commit_file(
        _PATH, "one\ntwo\nthree\nfour\nFIVE\n", "agent edit", author="Agent <a@x.com>"
    )

    coedit_checkpoint.checkpoint_session(sess.id)

    merged = "ONE\ntwo\nthree\nfour\nFIVE\n"
    assert wiki_git.read_file(_PATH) == merged
    st = coedit.get_active_session(_PATH)
    assert st is not None
    assert st.buffer_text == merged  # buffer synced to the merged result
    assert st.checkpointed_version == st.version  # clean

    # A second checkpoint is a clean no-op — the agent's edit is retained, not
    # dropped by re-committing a stale buffer.
    assert coedit_checkpoint.checkpoint_session(sess.id) is None
    assert wiki_git.read_file(_PATH) == merged


def test_checkpoint_raced_fallback_leaves_session_dirty(repo, monkeypatch):
    # If a human op races in during the commit (reconcile CAS misses), the
    # fallback must NOT advance base_sha / checkpointed_version — that would make
    # the next checkpoint's merge base==current and drop the agent's edit. Leaving
    # the session dirty at its old base lets the next checkpoint 3-way merge
    # preserve both. Here we assert that invariant.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    doc = "one\ntwo\nthree\nfour\nfive\n"
    sha = _seed_page(doc)
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer=doc)
    coedit.join(sess.id, uid)
    coedit.apply_op(sess.id, base_version=0, changes=[_ch(0, 3, "ONE")], author_user_id=uid)
    wiki_git.commit_file(
        _PATH, "one\ntwo\nthree\nfour\nFIVE\n", "agent edit", author="Agent <a@x.com>"
    )

    # Simulate the race: rebase_onto's CAS misses.
    monkeypatch.setattr(coedit, "rebase_onto", lambda *a, **k: None)
    coedit_checkpoint.checkpoint_session(sess.id)

    st = coedit.get_active_session(_PATH)
    assert st is not None
    assert st.base_sha == sha  # NOT advanced past the buffer's ancestor
    assert st.version > st.checkpointed_version  # still dirty → next checkpoint reconciles


def test_checkpoint_is_noop_when_clean(repo):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer="hello world")
    coedit.join(sess.id, uid)
    coedit.apply_op(sess.id, base_version=0, changes=[_ch(0, 5, "hi")], author_user_id=uid)
    assert coedit_checkpoint.checkpoint_session(sess.id) is not None
    # Nothing new since the last checkpoint → no second commit.
    assert coedit_checkpoint.checkpoint_session(sess.id) is None


def test_checkpoint_merges_concurrent_agent_commit(repo):
    # Distant, non-overlapping edits so git merge-file resolves cleanly (no LLM):
    # the buffer edits the first line, the agent edits the last, lines apart.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    doc = "one\ntwo\nthree\nfour\nfive\n"
    sha = _seed_page(doc)
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer=doc)
    coedit.join(sess.id, uid)
    # Human edits the first line in the buffer ("one" → "ONE")...
    coedit.apply_op(sess.id, base_version=0, changes=[_ch(0, 3, "ONE")], author_user_id=uid)
    # ...while an agent commits a change to the last line out of band.
    wiki_git.commit_file(
        _PATH, "one\ntwo\nthree\nfour\nFIVE\n", "agent edit", author="Agent <a@x.com>"
    )

    coedit_checkpoint.checkpoint_session(sess.id)

    # 3-way merge keeps both non-overlapping edits.
    assert wiki_git.read_file(_PATH) == "ONE\ntwo\nthree\nfour\nFIVE\n"


def test_checkpoint_clears_template_draft_when_body_diverges(repo):
    # A page created from a template has a document_drafts row; once a human's
    # committed edit diverges from the template snapshot, the row must clear.
    # Human edits commit via the checkpoint (not PUT /file), so the checkpoint
    # must do this — mirroring the PUT /file save path.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    tmpl_body = "# Template\n"
    with db_session() as s:
        s.add(DocumentTemplate(id="tmpl_x", name="X", body=tmpl_body))
    sha = _seed_page(tmpl_body)
    drafts.create(
        path=_PATH, template_id="tmpl_x", template_body_snapshot=tmpl_body, created_by_user_id=uid
    )
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer=tmpl_body)
    coedit.join(sess.id, uid)
    coedit.apply_op(
        sess.id, base_version=0, changes=[_ch(0, len(tmpl_body), "# Mine\n")], author_user_id=uid
    )

    coedit_checkpoint.checkpoint_session(sess.id)

    assert drafts.get(_PATH) is None  # diverged from the template → row cleared


def test_task_checkpoints_then_closes_when_empty(repo):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer="hello world")
    coedit.join(sess.id, uid)
    coedit.apply_op(sess.id, base_version=0, changes=[_ch(0, 5, "hi")], author_user_id=uid)
    coedit.leave(sess.id, uid)  # last participant gone

    with documents_queue.immediate_mode():
        coedit_checkpoint_task.checkpoint_coedit_session(sess.id)

    assert wiki_git.read_file(_PATH) == "hi world"
    # No participants remained → the task closed the session.
    assert coedit.get_active_session(_PATH) is None


def test_task_keeps_session_open_when_participants_remain(repo):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer="hello world")
    coedit.join(sess.id, uid)
    coedit.apply_op(sess.id, base_version=0, changes=[_ch(0, 5, "hi")], author_user_id=uid)

    with documents_queue.immediate_mode():
        coedit_checkpoint_task.checkpoint_coedit_session(sess.id)

    assert wiki_git.read_file(_PATH) == "hi world"
    # A participant is still editing → session stays active.
    st = coedit.get_active_session(_PATH)
    assert st is not None
    assert st.checkpointed_version == 1


def test_scan_checkpoints_due_sessions(repo, monkeypatch):
    # Force the scan's idle threshold to zero so a just-edited session is due.
    monkeypatch.setattr(coedit_checkpoint_task, "_IDLE_SECONDS", 0)
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer="hello world")
    coedit.join(sess.id, uid)
    coedit.apply_op(sess.id, base_version=0, changes=[_ch(0, 5, "hi")], author_user_id=uid)
    coedit.leave(sess.id, uid)

    with documents_queue.immediate_mode():
        coedit_checkpoint_task.scan_and_checkpoint()

    assert wiki_git.read_file(_PATH) == "hi world"
    assert coedit.get_active_session(_PATH) is None


def test_commit_message_credits_other_participants_as_coauthors(repo):
    a = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    b = users_repo.create(email="bo@x.com", password="hunter2-x", name="Bo")
    sess = coedit.open_session(_PATH, base_sha=None)
    coedit.join(sess.id, a)
    coedit.join(sess.id, b)
    # Bo is the primary author (git author); Ada is credited as a co-author.
    msg = coedit_checkpoint._commit_message(sess.id, primary_author_id=b)
    assert "Co-authored-by: Ada <ada@x.com>" in msg
    assert "bo@x.com" not in msg
