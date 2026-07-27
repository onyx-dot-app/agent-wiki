"""Checkpoint engine (app/wiki/coedit_checkpoint.py) — reconstructs a
session's live Yjs doc and commits it to git via the merge gateway, with
last-editor author + co-author trailers. DB + real git (the ``tmp_repo``
fixture) + an in-process room (``coedit_room``) — the live document lives
only there, never in the DB (see ``app/wiki/coedit.py``'s module docstring).

``checkpoint_session`` (and the task wrapper ``checkpoint_coedit_session``)
are ``async`` — this codebase has no ``pytest-asyncio`` plugin, so tests
drive them via a small ``async def run(): ...`` + ``asyncio.run(run())``,
the established pattern (see ``test_mcp_pubsub.py``). That's safe here
specifically because ``asyncio.run`` doesn't spawn a new OS thread — the
loop runs on the calling (main) thread, the same thread every room/Doc in
these tests is created and edited on, so there's no cross-thread Doc access
despite the async plumbing (see ``coedit_room.py`` on why that matters).
"""
from __future__ import annotations

import asyncio
import os

import pytest
from pycrdt import XmlElement, XmlFragment, XmlText
from sqlalchemy import text

from app.auth import users as users_repo
from app.db.models import DocumentTemplate
from app.db.session import session as db_session
from app.db.session import try_advisory_xact_lock
from app.tasks import coedit_checkpoint as coedit_checkpoint_task
from app.wiki import coedit, coedit_checkpoint, coedit_room, drafts
from app.wiki import git as wiki_git
from app.wiki.markdown_yjs import ROOT_XML_KEY
from app.models.wiki import PathMove

_PATH = "guides/setup.md"


def _seed_page(body: str) -> str:
    return wiki_git.commit_file(_PATH, body, "seed", author="Seed <seed@x.com>")


@pytest.fixture
def repo(tmp_repo):
    return tmp_repo


def _root(doc):
    # No return-type annotation, matching test_markdown_splice.py's _root —
    # an explicit `-> XmlFragment` here makes basedpyright actually check
    # `.children[i].insert(...)` against the full XmlText|XmlElement|
    # XmlFragment union (only XmlText has .insert) and flag a false
    # positive; left inferred, the chain stays Unknown and passes.
    return doc.get(ROOT_XML_KEY, type=XmlFragment)


def _room(sess, body: str) -> coedit_room.Room:
    return coedit_room.create_room(sess.id, sess.path, body, sess.base_sha)


def _edit(sess, room, uid: str, prefix: str) -> None:
    """Prepend ``prefix`` to the doc's first paragraph and record a matching
    DB update — mirrors what the WS route does on a real edit (apply to the
    Doc, log the update), split into two explicit steps since these tests
    drive the Doc directly rather than through pycrdt's wire protocol."""
    root = _root(room.doc)
    with room.doc.transaction():
        root.children[0].children[0].insert(0, prefix)
    coedit.apply_update(sess.id, update_bytes=prefix.encode(), author_user_id=uid)


def _new_page(sess, uid: str, text_: str) -> coedit_room.Room:
    """For a session with no base content (base_sha=None, page doesn't exist
    yet): the room's doc starts with zero blocks, so there's no existing
    paragraph to prepend into — a whole new block has to be constructed
    directly, same pattern as
    test_markdown_splice.py's new-top-level-block test."""
    room = _room(sess, "")
    root = _root(room.doc)
    para = XmlElement("paragraph", {"_blockId": "new0", "_nl": "1"}, contents=[])
    with room.doc.transaction():
        root.children.append(para)
    with room.doc.transaction():
        para.children.append(XmlText(text_))
    coedit.apply_update(sess.id, update_bytes=text_.encode(), author_user_id=uid)
    return room


def _run(coro):
    return asyncio.run(coro)


def test_checkpoint_commits_and_attributes_last_editor(repo):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    room = _room(sess, "hello world")
    _edit(sess, room, uid, "EDITED ")

    new_sha = _run(coedit_checkpoint.checkpoint_session(sess.id))

    assert new_sha is not None
    assert wiki_git.read_file(_PATH) == "EDITED hello world"
    st = coedit.get_active_session(_PATH)
    assert st is not None
    assert st.ydoc_checkpointed_seq == 1
    assert st.base_sha == new_sha
    # git author is the last editor.
    assert wiki_git.history(_PATH)[0].author == "Ada"


def test_checkpoint_reseeds_room_and_syncs_merged_result(repo):
    # When the checkpoint's 3-way merge folds in a concurrent agent commit,
    # the room must be re-seeded from the merged result — otherwise a later
    # checkpoint would re-commit the pre-merge doc and silently drop the
    # agent's edit.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    doc = "one\ntwo\nthree\nfour\nfive\n"
    sha = _seed_page(doc)
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    room = _room(sess, doc)
    # Human edits inside the (single, soft-break-joined) paragraph; agent
    # commits a distant, non-overlapping change out of band, against the
    # original HEAD, unaware of the human's in-session edit.
    _edit(sess, room, uid, "EDIT-")
    wiki_git.commit_file(_PATH, "one\ntwo\nthree\nfour\nFIVE\n", "agent edit", author="Agent <a@x.com>")

    _run(coedit_checkpoint.checkpoint_session(sess.id))

    merged = "EDIT-one\ntwo\nthree\nfour\nFIVE\n"
    assert wiki_git.read_file(_PATH) == merged
    st = coedit.get_active_session(_PATH)
    assert st is not None
    assert st.ydoc_checkpointed_seq == st.ydoc_seq  # clean
    live_room = coedit_room.get_room(sess.id)
    assert live_room is not None
    assert live_room.base_body == merged  # room re-seeded to the merged result

    # A second checkpoint is a clean no-op — the agent's edit is retained,
    # not dropped by re-committing a stale doc.
    assert _run(coedit_checkpoint.checkpoint_session(sess.id)) is None
    assert wiki_git.read_file(_PATH) == merged


def test_checkpoint_survives_a_racing_close(repo, monkeypatch):
    # If the session is closed by something else between the commit and the
    # DB bookkeeping that follows it (coedit.rebase_onto returns None), the
    # checkpoint must not raise — the git commit already landed and is the
    # real source of truth; the session is dead either way, so its DB row
    # not advancing further is harmless.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    room = _room(sess, "hello world")
    _edit(sess, room, uid, "EDITED ")

    monkeypatch.setattr(coedit, "rebase_onto", lambda *a, **k: None)
    new_sha = _run(coedit_checkpoint.checkpoint_session(sess.id))

    assert new_sha is not None
    assert wiki_git.read_file(_PATH) == "EDITED hello world"


def test_checkpoint_is_noop_when_clean(repo):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    room = _room(sess, "hello world")
    _edit(sess, room, uid, "EDITED ")
    assert _run(coedit_checkpoint.checkpoint_session(sess.id)) is not None
    # Nothing new since the last checkpoint → no second commit.
    assert _run(coedit_checkpoint.checkpoint_session(sess.id)) is None


def test_checkpoint_clears_template_draft_when_body_diverges(repo):
    # A page created from a template has a document_drafts row; once a
    # human's committed edit diverges from the template snapshot, the row
    # must clear. Human edits commit via the checkpoint (not PUT /file), so
    # the checkpoint must do this — mirroring the PUT /file save path.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    tmpl_body = "# Template\n"
    with db_session() as s:
        s.add(DocumentTemplate(id="tmpl_x", name="X", body=tmpl_body))
    sha = _seed_page(tmpl_body)
    drafts.create(
        path=_PATH, template_id="tmpl_x", template_body_snapshot=tmpl_body, created_by_user_id=uid
    )
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    room = _room(sess, tmpl_body)
    _edit(sess, room, uid, "Mine: ")

    _run(coedit_checkpoint.checkpoint_session(sess.id))

    assert drafts.get(_PATH) is None  # diverged from the template → row cleared


def test_task_checkpoints_then_closes_when_empty(repo):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    room = _room(sess, "hello world")
    _edit(sess, room, uid, "EDITED ")
    coedit.leave(sess.id, uid)  # last participant gone

    _run(coedit_checkpoint_task.checkpoint_coedit_session(sess.id))

    assert wiki_git.read_file(_PATH) == "EDITED hello world"
    # No participants remained → the task closed the session.
    assert coedit.get_active_session(_PATH) is None


def test_task_keeps_session_open_when_participants_remain(repo):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    room = _room(sess, "hello world")
    _edit(sess, room, uid, "EDITED ")

    _run(coedit_checkpoint_task.checkpoint_coedit_session(sess.id))

    assert wiki_git.read_file(_PATH) == "EDITED hello world"
    # A participant is still editing → session stays active.
    st = coedit.get_active_session(_PATH)
    assert st is not None
    assert st.ydoc_checkpointed_seq == 1


def test_scan_checkpoints_due_sessions(repo, monkeypatch):
    # Force the scan's idle threshold to zero so a just-edited session is due.
    monkeypatch.setattr(coedit_checkpoint_task, "_IDLE_SECONDS", 0)
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    room = _room(sess, "hello world")
    _edit(sess, room, uid, "EDITED ")
    coedit.leave(sess.id, uid)

    _run(coedit_checkpoint_task.scan_once())

    assert wiki_git.read_file(_PATH) == "EDITED hello world"
    assert coedit.get_active_session(_PATH) is None


def test_scan_ignores_sessions_without_a_local_room(repo, monkeypatch):
    # A dirty session whose room lives in a different process is not this
    # process's to checkpoint — the scan must skip it, not crash trying to
    # read a room it doesn't have.
    monkeypatch.setattr(coedit_checkpoint_task, "_IDLE_SECONDS", 0)
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    coedit.apply_update(sess.id, update_bytes=b"x", author_user_id=uid)  # dirty, but no local room

    _run(coedit_checkpoint_task.scan_once())

    # Untouched — nothing committed, session still active and dirty.
    assert wiki_git.read_file(_PATH) == "hello world"
    st = coedit.get_active_session(_PATH)
    assert st is not None
    assert st.ydoc_seq != st.ydoc_checkpointed_seq


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
    # A closed session with un-checkpointed edits must NOT be re-committed —
    # it would clobber newer HEAD with stale content (the 2026-07-06
    # incident). No room needed: the closed+dirty check short-circuits
    # before this engine ever looks at the room registry.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    coedit.apply_update(sess.id, update_bytes=b"x", author_user_id=uid)
    # An agent lands a newer commit after the session's base.
    newer = wiki_git.commit_file(_PATH, "hello world v2", "agent", author="A <a@x.com>")
    coedit.close_session(sess.id)  # closed while still dirty

    assert _run(coedit_checkpoint.checkpoint_session(sess.id)) is None  # skipped
    # HEAD is untouched — nothing clobbered the agent's commit.
    assert wiki_git.head_sha_for_path(_PATH) == newer
    assert wiki_git.read_file(_PATH) == "hello world v2"


def test_duplicate_checkpoints_commit_once(repo):
    # Several checkpoint attempts for the same session must produce ONE
    # commit: the first commits + closes; the rest no-op on the now-closed
    # session (previously each re-committed → the incident's 4x clobber).
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    room = _room(sess, "hello world")
    _edit(sess, room, uid, "EDITED ")
    coedit.leave(sess.id, uid)  # last participant gone → eligible to close

    before = len(wiki_git.history(_PATH))

    async def run():
        await coedit_checkpoint_task.checkpoint_coedit_session(sess.id)  # commits + closes
        await coedit_checkpoint_task.checkpoint_coedit_session(sess.id)  # duplicate → no-op
        await coedit_checkpoint_task.checkpoint_coedit_session(sess.id)  # duplicate → no-op

    _run(run())
    after = len(wiki_git.history(_PATH))
    assert after - before == 1  # exactly one "Co-editing checkpoint" commit
    assert wiki_git.read_file(_PATH) == "EDITED hello world"


def test_checkpoint_lock_serializes_same_session_only(repo):
    # The advisory lock is what makes concurrency > 1 safe: while one holder
    # has a session's checkpoint lock, another connection can't take the
    # same session's key (so a concurrent duplicate blocks, then no-ops),
    # but a *different* session's key is free (distinct sessions checkpoint
    # in parallel). Session ids are offset by PID so parallel xdist workers
    # don't collide on the DB-global advisory keyspace.
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
    # A duplicate checkpoint that can't get the lock within the cap yields
    # False (→ the caller skips and no-ops) rather than blocking forever.
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
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)

    new_path = "guides/install.md"
    coedit.on_path_moved([PathMove(old=_PATH, new=new_path)])

    assert coedit.get_active_session(_PATH) is None
    moved = coedit.get_active_session(new_path)
    assert moved is not None and moved.id == sess.id


def test_on_path_moved_folder_rename_carries_sessions(repo):
    sha = wiki_git.commit_file("a/deep/page.md", "body", "seed", author=None)
    sess = coedit.open_session("a/deep/page.md", base_sha=sha)

    coedit.on_path_moved([PathMove(old="a/deep/page.md", new="b/deep/page.md")])

    moved = coedit.get_active_session("b/deep/page.md")
    assert moved is not None and moved.id == sess.id


def test_checkpoint_commits_to_new_path_after_move(repo):
    uid = users_repo.create(email="eve@x.com", password="hunter2-x", name="Eve")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    room = _room(sess, "hello world")
    _edit(sess, room, uid, "HOWDY ")

    new_path = "guides/install.md"
    wiki_git.move_path(_PATH, new_path, "rename")
    coedit.on_path_moved([PathMove(old=_PATH, new=new_path)])

    new_sha = _run(coedit_checkpoint.checkpoint_session(sess.id))
    assert new_sha is not None
    # The doc landed on the page's new home; the old path stays gone.
    assert wiki_git.read_file_opt(new_path) == "HOWDY hello world"
    assert wiki_git.read_file_opt(_PATH) is None


def test_checkpoint_closes_session_when_path_gone(repo):
    uid = users_repo.create(email="zoe@x.com", password="hunter2-x", name="Zoe")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    room = _room(sess, "hello world")
    _edit(sess, room, uid, "STALE ")

    # The page moves away but the session is NOT re-keyed (the pre-fix bug,
    # or a raw-git rename that bypassed the lifecycle hook). The editor
    # left, so the session is participant-less — the zombie case the guard
    # closes.
    coedit.leave(sess.id, uid)
    wiki_git.move_path(_PATH, "guides/renamed.md", "rename")

    assert _run(coedit_checkpoint.checkpoint_session(sess.id)) is None
    # No resurrection at the dead path, and the session is closed. (Unlike
    # the OT era, nothing persists the lost edit for manual recovery — the
    # live doc only ever existed in this process's room; see coedit.py's
    # module docstring.)
    assert wiki_git.read_file_opt(_PATH) is None
    after = coedit.get_session(sess.id)
    assert after is not None
    assert after.status == coedit.SessionStatus.CLOSED.value


def test_checkpoint_still_creates_brand_new_page(repo):
    # base_sha None = the session legitimately started on a not-yet-committed
    # page; the missing-path guard must not block the create flow.
    uid = users_repo.create(email="new@x.com", password="hunter2-x", name="New")
    sess = coedit.open_session("guides/fresh.md", base_sha=None)
    coedit.join(sess.id, uid)
    _new_page(sess, uid, "first words")

    assert _run(coedit_checkpoint.checkpoint_session(sess.id)) is not None
    assert wiki_git.read_file_opt("guides/fresh.md") == "first words"


def test_checkpoint_skips_but_keeps_session_with_participants(repo):
    uid = users_repo.create(email="liv@x.com", password="hunter2-x", name="Liv")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    room = _room(sess, "hello world")
    _edit(sess, room, uid, "LIVE ")

    # Path vanishes mid-session while someone is still editing (e.g. a move
    # whose re-key hasn't landed yet): skip, don't close — the scan retries.
    wiki_git.move_path(_PATH, "guides/renamed.md", "rename")

    assert _run(coedit_checkpoint.checkpoint_session(sess.id)) is None
    assert wiki_git.read_file_opt(_PATH) is None
    after = coedit.get_session(sess.id)
    assert after is not None and after.status == coedit.SessionStatus.ACTIVE.value


def test_on_path_moved_leaves_siblings_alone(repo):
    # A single cross-folder page move must not re-key sessions of unmoved
    # siblings in the same folder (prefix matching would).
    sha_a = wiki_git.commit_file("a/deep/page.md", "moving", "seed", author=None)
    sha_b = wiki_git.commit_file("a/deep/other.md", "staying", "seed", author=None)
    moved = coedit.open_session("a/deep/page.md", base_sha=sha_a)
    sibling = coedit.open_session("a/deep/other.md", base_sha=sha_b)

    coedit.on_path_moved([PathMove(old="a/deep/page.md", new="b/deep/page.md")])

    got_moved = coedit.get_active_session("b/deep/page.md")
    assert got_moved is not None and got_moved.id == moved.id
    got_sibling = coedit.get_active_session("a/deep/other.md")
    assert got_sibling is not None and got_sibling.id == sibling.id


def test_on_path_moved_origin_wins_over_young_dirty_destination(repo):
    uid = users_repo.create(email="sq@x.com", password="hunter2-x", name="Sq")
    sha = _seed_page("origin")
    origin = coedit.open_session(_PATH, base_sha=sha)
    # A session opened at the destination inside the move window collected a
    # few keystrokes. Long-lived drafts can't get here — the move's 409
    # pre-check (blocking_active_session_path) rejects them before git mv.
    young = coedit.open_session("guides/target.md", base_sha=None)
    coedit.join(young.id, uid)
    coedit.apply_update(young.id, update_bytes=b"few chars", author_user_id=uid)

    coedit.on_path_moved([PathMove(old=_PATH, new="guides/target.md")])

    # The origin session follows the page; the young session closes.
    at_dest = coedit.get_active_session("guides/target.md")
    assert at_dest is not None and at_dest.id == origin.id
    superseded = coedit.get_session(young.id)
    assert superseded is not None
    assert superseded.status == coedit.SessionStatus.CLOSED.value


def test_on_path_moved_supersedes_clean_destination_session(repo):
    uid = users_repo.create(email="ed@x.com", password="hunter2-x", name="Ed")
    sha = _seed_page("origin body")
    origin = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(origin.id, uid)
    coedit.apply_update(origin.id, update_bytes=b"edited", author_user_id=uid)
    # Someone opened the just-moved page at its new home before the re-key
    # ran: a clean session seeded from the moved content.
    fresh = coedit.open_session("guides/target.md", base_sha=sha)

    coedit.on_path_moved([PathMove(old=_PATH, new="guides/target.md")])

    # The dirty origin session follows the page; the clean fresh session closes.
    at_dest = coedit.get_active_session("guides/target.md")
    assert at_dest is not None and at_dest.id == origin.id
    superseded = coedit.get_session(fresh.id)
    assert superseded is not None
    assert superseded.status == coedit.SessionStatus.CLOSED.value


def test_blocking_active_session_path(repo):
    assert coedit.blocking_active_session_path("guides/new-home.md") is None
    coedit.open_session("guides/new-home.md", base_sha=None)
    # Exact page destination blocks; a folder destination blocks on nested paths.
    assert coedit.blocking_active_session_path("guides/new-home.md") == "guides/new-home.md"
    assert coedit.blocking_active_session_path("guides") == "guides/new-home.md"
    assert coedit.blocking_active_session_path("other") is None
