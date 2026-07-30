"""Checkpoint engine (app/wiki/coedit_checkpoint.py) — commits a session's live
buffer to git via the merge gateway, with last-editor author + co-author
trailers. DB + real git (the ``tmp_repo`` fixture).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text, update

from app.auth import users as users_repo
from app.db.models import CoeditParticipant, CoeditSession, DocumentTemplate
from app.db.session import session as db_session
from app.db.session import try_advisory_xact_lock
from app.tasks import coedit_checkpoint as coedit_checkpoint_task
from app.tasks.queues import coedit_queue
from app.wiki import coedit, coedit_checkpoint, drafts
from app.wiki import git as wiki_git
from app.models.wiki import PathMove

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


def _racing_rebase(monkeypatch, author_user_id: str, change: coedit.Change):
    """Land ``change`` between the commit and its write-back, so the CAS misses.

    Typing through a checkpoint is the ordinary case — the commit takes long
    enough (a git write, sometimes an LLM merge) that a keystroke lands inside
    it — so this is the common path, not a rare interleaving.
    """
    real_rebase = coedit.rebase_onto

    def racing(session_id, **kwargs):
        coedit.apply_op(
            session_id,
            base_version=kwargs["base_version"],
            changes=[change],
            author_user_id=author_user_id,
        )
        return real_rebase(session_id, **kwargs)

    monkeypatch.setattr(coedit, "rebase_onto", racing)
    return real_rebase


def test_raced_checkpoint_advances_watermark_and_converges(repo, monkeypatch):
    # A raced write-back with nothing foreign folded in: the commit *is* the
    # buffer at the version we read, so the watermark records it. The session
    # stays dirty for the keystroke that raced in, and the next checkpoint
    # converges on it — merging from the sha just written rather than an
    # ever-older base, which is what duplicated and dropped text.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer="hello world")
    coedit.join(sess.id, uid)
    coedit.apply_op(sess.id, base_version=0, changes=[_ch(0, 5, "hi")], author_user_id=uid)

    real_rebase = _racing_rebase(monkeypatch, uid, _ch(8, 8, "!"))
    assert coedit_checkpoint.checkpoint_session(sess.id) is not None
    monkeypatch.setattr(coedit, "rebase_onto", real_rebase)

    st = coedit.get_active_session(_PATH)
    assert st is not None
    assert wiki_git.read_file(_PATH) == "hi world"
    assert st.base_sha == wiki_git.head_sha_for_path(_PATH)
    assert st.checkpointed_version == 1
    assert st.last_checkpoint_at is not None
    assert st.version == 2  # the raced keystroke is still uncommitted

    assert coedit_checkpoint.checkpoint_session(sess.id) is not None
    assert wiki_git.read_file(_PATH) == "hi world!"
    st = coedit.get_active_session(_PATH)
    assert st is not None and st.version == st.checkpointed_version


def test_raced_checkpoint_stops_reenqueueing_every_scan(repo, monkeypatch):
    # The scan's overdue arm measures from last_checkpoint_at, so a raced
    # checkpoint that recorded nothing left the session overdue forever: every
    # scan re-enqueued it, and each attempt committed again.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer="hello world")
    coedit.join(sess.id, uid)
    coedit.apply_op(sess.id, base_version=0, changes=[_ch(0, 5, "hi")], author_user_id=uid)
    # Age it past the overdue threshold; never-checkpointed sessions measure from
    # created_at.
    aged = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with db_session() as s:
        s.execute(update(CoeditSession).where(CoeditSession.id == sess.id).values(created_at=aged))

    def due() -> list[int]:
        return [
            d.id
            for d in coedit.sessions_due_for_checkpoint(
                idle_seconds=3600, max_interval_seconds=60
            )
        ]

    assert due() == [sess.id]

    real_rebase = _racing_rebase(monkeypatch, uid, _ch(8, 8, "!"))
    coedit_checkpoint.checkpoint_session(sess.id)
    monkeypatch.setattr(coedit, "rebase_onto", real_rebase)

    # Still dirty from the raced keystroke, but no longer overdue — the next
    # commit waits for the interval instead of firing on every scan.
    st = coedit.get_active_session(_PATH)
    assert st is not None and st.version > st.checkpointed_version
    assert due() == []


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

    with coedit_queue.immediate_mode():
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

    with coedit_queue.immediate_mode():
        coedit_checkpoint_task.checkpoint_coedit_session(sess.id)

    assert wiki_git.read_file(_PATH) == "hi world"
    # A participant is still editing → session stays active.
    st = coedit.get_active_session(_PATH)
    assert st is not None
    assert st.checkpointed_version == 1


def test_scan_checkpoints_due_sessions(repo, monkeypatch):
    monkeypatch.setattr(coedit_checkpoint_task, "_IDLE_SECONDS", 0)
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer="hello world")
    coedit.join(sess.id, uid)
    coedit.apply_op(sess.id, base_version=0, changes=[_ch(0, 5, "hi")], author_user_id=uid)
    coedit.leave(sess.id, uid)

    with coedit_queue.immediate_mode():
        coedit_checkpoint_task.scan_and_checkpoint()

    assert wiki_git.read_file(_PATH) == "hi world"
    assert coedit.get_active_session(_PATH) is None


def test_scan_expires_last_participant_and_checkpoints(repo):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer="hello world")
    coedit.join(sess.id, uid)
    coedit.apply_op(sess.id, base_version=0, changes=[_ch(0, 5, "hi")], author_user_id=uid)
    with db_session() as db:
        db.execute(
            update(CoeditParticipant)
            .where(
                CoeditParticipant.session_id == sess.id,
                CoeditParticipant.user_id == uid,
            )
            .values(last_seen_at="2000-01-01T00:00:00+00:00")
        )

    with coedit_queue.immediate_mode():
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


def test_checkpoint_skips_closed_dirty_session(repo):
    # A closed session with un-checkpointed edits must NOT be re-committed — it
    # would clobber newer HEAD with a stale buffer (the 2026-07-06 incident).
    # Its edits stay in the buffer for manual recovery.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer="hello world")
    coedit.join(sess.id, uid)
    coedit.apply_op(sess.id, base_version=0, changes=[_ch(0, 5, "hi")], author_user_id=uid)
    # An agent lands a newer commit after the buffer's base.
    newer = wiki_git.commit_file(_PATH, "hello world v2", "agent", author="A <a@x.com>")
    coedit.close_session(sess.id)  # closed while still dirty (v1, checkpointed v0)

    assert coedit_checkpoint.checkpoint_session(sess.id) is None  # skipped
    # HEAD is untouched — the stale buffer did not clobber the agent's commit.
    assert wiki_git.head_sha_for_path(_PATH) == newer
    assert wiki_git.read_file(_PATH) == "hello world v2"


def test_duplicate_queued_checkpoints_commit_once(repo):
    # Two queued checkpoint tasks for the same session must produce ONE commit:
    # the first commits + closes; the second no-ops on the now-closed session
    # (previously each re-committed → the incident's 4x clobber).
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer="hello world")
    coedit.join(sess.id, uid)
    coedit.apply_op(sess.id, base_version=0, changes=[_ch(0, 5, "hi")], author_user_id=uid)
    coedit.leave(sess.id, uid)  # last participant gone → eligible to close

    before = len(wiki_git.history(_PATH))
    with coedit_queue.immediate_mode():
        coedit_checkpoint_task.checkpoint_coedit_session(sess.id)  # commits + closes
        coedit_checkpoint_task.checkpoint_coedit_session(sess.id)  # duplicate → no-op
        coedit_checkpoint_task.checkpoint_coedit_session(sess.id)  # duplicate → no-op
    after = len(wiki_git.history(_PATH))
    assert after - before == 1  # exactly one "Co-editing checkpoint" commit
    assert wiki_git.read_file(_PATH) == "hi world"


def test_checkpoint_lock_serializes_same_session_only(repo):
    # The advisory lock is what makes concurrency > 1 safe: while one worker
    # holds a session's checkpoint lock, another connection can't take the same
    # session's key (so a concurrent duplicate blocks, then no-ops), but a
    # *different* session's key is free (distinct sessions checkpoint in
    # parallel). Session ids are offset by PID so parallel xdist workers don't
    # collide on the DB-global advisory keyspace.
    base = 1_000_000 + os.getpid() % 1_000_000
    sid, other = base, base + 1

    def try_lock(s, session_id: int) -> bool:
        return bool(
            s.execute(
                text("SELECT pg_try_advisory_xact_lock(:k)"),
                {"k": coedit.checkpoint_lock_key(session_id)},
            ).scalar()
        )

    with coedit.checkpoint_lock(sid) as acquired:
        assert acquired is True  # uncontended → held
        with db_session() as s2:  # a second connection
            assert try_lock(s2, sid) is False  # same session's key is held
            assert try_lock(s2, other) is True  # a different session's is free
    # Released on context exit (transaction-scoped) — the key is takeable again.
    with db_session() as s3:
        assert try_lock(s3, sid) is True


def test_checkpoint_lock_times_out_when_held(repo):
    # A duplicate checkpoint that can't get the lock within the cap yields False
    # (→ the task skips and no-ops) rather than blocking forever.
    base = 2_000_000 + os.getpid() % 1_000_000
    with coedit.checkpoint_lock(base) as acquired:
        assert acquired is True
        with db_session() as s2:
            got = try_advisory_xact_lock(
                s2, coedit.checkpoint_lock_key(base), timeout_ms=100
            )
        assert got is False  # held elsewhere → bounded wait elapsed


# --------------------------------------------------------------------------- #
# Page moves: sessions follow the page; dead paths never resurrect            #
# --------------------------------------------------------------------------- #


def test_on_path_moved_rekeys_session(repo):
    uid = users_repo.create(email="mia@x.com", password="hunter2-x", name="Mia")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer="hello world")
    coedit.join(sess.id, uid)

    new_path = "guides/install.md"
    coedit.on_path_moved([PathMove(old=_PATH, new=new_path)])

    assert coedit.get_active_session(_PATH) is None
    moved = coedit.get_active_session(new_path)
    assert moved is not None and moved.id == sess.id


def test_on_path_moved_folder_rename_carries_sessions(repo):
    sha = wiki_git.commit_file("a/deep/page.md", "body", "seed", author=None)
    sess = coedit.open_session("a/deep/page.md", base_sha=sha, initial_buffer="body")

    coedit.on_path_moved([PathMove(old="a/deep/page.md", new="b/deep/page.md")])

    moved = coedit.get_active_session("b/deep/page.md")
    assert moved is not None and moved.id == sess.id


def test_checkpoint_commits_to_new_path_after_move(repo):
    uid = users_repo.create(email="eve@x.com", password="hunter2-x", name="Eve")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer="hello world")
    coedit.join(sess.id, uid)
    coedit.apply_op(sess.id, base_version=0, changes=[_ch(0, 5, "howdy")], author_user_id=uid)

    new_path = "guides/install.md"
    wiki_git.move_path(_PATH, new_path, "rename")
    coedit.on_path_moved([PathMove(old=_PATH, new=new_path)])

    new_sha = coedit_checkpoint.checkpoint_session(sess.id)
    assert new_sha is not None
    # The buffer landed on the page's new home; the old path stays gone.
    assert wiki_git.read_file_opt(new_path) == "howdy world"
    assert wiki_git.read_file_opt(_PATH) is None


def test_checkpoint_closes_session_when_path_gone(repo):
    uid = users_repo.create(email="zoe@x.com", password="hunter2-x", name="Zoe")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer="hello world")
    coedit.join(sess.id, uid)
    coedit.apply_op(sess.id, base_version=0, changes=[_ch(0, 5, "stale")], author_user_id=uid)

    # The page moves away but the session is NOT re-keyed (the pre-fix bug, or
    # a raw-git rename that bypassed the lifecycle hook). The editor left, so
    # the session is participant-less — the zombie case the guard closes.
    coedit.leave(sess.id, uid)
    wiki_git.move_path(_PATH, "guides/renamed.md", "rename")

    assert coedit_checkpoint.checkpoint_session(sess.id) is None
    # No resurrection at the dead path, and the session is closed with the
    # buffer preserved for manual recovery.
    assert wiki_git.read_file_opt(_PATH) is None
    after = coedit.get_session(sess.id)
    assert after is not None
    assert after.status == coedit.SessionStatus.CLOSED.value
    assert after.buffer_text == "stale world"


def test_checkpoint_still_creates_brand_new_page(repo):
    # base_sha None = the session legitimately started on a not-yet-committed
    # page; the missing-path guard must not block the create flow.
    uid = users_repo.create(email="new@x.com", password="hunter2-x", name="New")
    sess = coedit.open_session("guides/fresh.md", base_sha=None, initial_buffer="")
    coedit.join(sess.id, uid)
    coedit.apply_op(sess.id, base_version=0, changes=[_ch(0, 0, "first words")], author_user_id=uid)

    assert coedit_checkpoint.checkpoint_session(sess.id) is not None
    assert wiki_git.read_file_opt("guides/fresh.md") == "first words"


def test_checkpoint_skips_but_keeps_session_with_participants(repo):
    uid = users_repo.create(email="liv@x.com", password="hunter2-x", name="Liv")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha, initial_buffer="hello world")
    coedit.join(sess.id, uid)
    coedit.apply_op(sess.id, base_version=0, changes=[_ch(0, 5, "live")], author_user_id=uid)

    # Path vanishes mid-session while someone is still editing (e.g. a move
    # whose re-key hasn't landed yet): skip, don't close — the scan retries.
    wiki_git.move_path(_PATH, "guides/renamed.md", "rename")

    assert coedit_checkpoint.checkpoint_session(sess.id) is None
    assert wiki_git.read_file_opt(_PATH) is None
    after = coedit.get_session(sess.id)
    assert after is not None and after.status == coedit.SessionStatus.ACTIVE.value


def test_on_path_moved_leaves_siblings_alone(repo):
    # A single cross-folder page move must not re-key sessions of unmoved
    # siblings in the same folder (prefix matching would).
    sha_a = wiki_git.commit_file("a/deep/page.md", "moving", "seed", author=None)
    sha_b = wiki_git.commit_file("a/deep/other.md", "staying", "seed", author=None)
    moved = coedit.open_session("a/deep/page.md", base_sha=sha_a, initial_buffer="moving")
    sibling = coedit.open_session("a/deep/other.md", base_sha=sha_b, initial_buffer="staying")

    coedit.on_path_moved([PathMove(old="a/deep/page.md", new="b/deep/page.md")])

    got_moved = coedit.get_active_session("b/deep/page.md")
    assert got_moved is not None and got_moved.id == moved.id
    got_sibling = coedit.get_active_session("a/deep/other.md")
    assert got_sibling is not None and got_sibling.id == sibling.id


def test_on_path_moved_origin_wins_over_young_dirty_destination(repo):
    uid = users_repo.create(email="sq@x.com", password="hunter2-x", name="Sq")
    sha = _seed_page("origin")
    origin = coedit.open_session(_PATH, base_sha=sha, initial_buffer="origin")
    # A session opened at the destination inside the move window collected a
    # few keystrokes. Long-lived drafts can't get here — the move's 409
    # pre-check (blocking_active_session_path) rejects them before git mv.
    young = coedit.open_session("guides/target.md", base_sha=None, initial_buffer="")
    coedit.join(young.id, uid)
    coedit.apply_op(young.id, base_version=0, changes=[_ch(0, 0, "few chars")], author_user_id=uid)

    coedit.on_path_moved([PathMove(old=_PATH, new="guides/target.md")])

    # The origin session follows the page; the young session closes with its
    # keystrokes preserved in the row.
    at_dest = coedit.get_active_session("guides/target.md")
    assert at_dest is not None and at_dest.id == origin.id
    superseded = coedit.get_session(young.id)
    assert superseded is not None
    assert superseded.status == coedit.SessionStatus.CLOSED.value
    assert superseded.buffer_text == "few chars"


def test_on_path_moved_supersedes_clean_destination_session(repo):
    uid = users_repo.create(email="ed@x.com", password="hunter2-x", name="Ed")
    sha = _seed_page("origin body")
    origin = coedit.open_session(_PATH, base_sha=sha, initial_buffer="origin body")
    coedit.join(origin.id, uid)
    coedit.apply_op(origin.id, base_version=0, changes=[_ch(0, 6, "edited")], author_user_id=uid)
    # Someone opened the just-moved page at its new home before the re-key ran:
    # a clean session seeded from the moved content.
    fresh = coedit.open_session("guides/target.md", base_sha=sha, initial_buffer="origin body")

    coedit.on_path_moved([PathMove(old=_PATH, new="guides/target.md")])

    # The dirty origin session follows the page; the clean fresh session closes.
    at_dest = coedit.get_active_session("guides/target.md")
    assert at_dest is not None and at_dest.id == origin.id
    assert at_dest.buffer_text == "edited body"
    superseded = coedit.get_session(fresh.id)
    assert superseded is not None
    assert superseded.status == coedit.SessionStatus.CLOSED.value


def test_blocking_active_session_path(repo):
    assert coedit.blocking_active_session_path("guides/new-home.md") is None
    coedit.open_session("guides/new-home.md", base_sha=None, initial_buffer="")
    # Exact page destination blocks; a folder destination blocks on nested paths.
    assert coedit.blocking_active_session_path("guides/new-home.md") == "guides/new-home.md"
    assert coedit.blocking_active_session_path("guides") == "guides/new-home.md"
    assert coedit.blocking_active_session_path("other") is None
