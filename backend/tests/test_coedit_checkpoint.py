"""Checkpoint engine (app/wiki/coedit_checkpoint.py) — reconstructs a
session's doc from its persisted (``ydoc_snapshot``, ``coedit_updates``)
state and commits it to git via the merge gateway, with last-editor author +
co-author trailers. DB + real git (the ``tmp_repo`` fixture).

There is no server-side replica of a session's document, so a checkpoint
rebuilds a throwaway ``Doc`` from the durable log and any process can run one.
``checkpoint_session``/``checkpoint_coedit_session_task``/
``scan_coedit_checkpoints`` are correspondingly plain sync — no event loop, no
``asyncio.run`` wrapper.

``_client`` stands in for a browser: a ``Doc`` the *test* holds, seeded the way
a first connection seeds one, whose edits reach the server as logged Yjs
updates via ``_edit``. They have to be real, applicable updates, since the
engine replays them through ``pycrdt``
(``coedit_checkpoint._rebuild_doc``). Each is a delta (``get_update(before)``),
which is what a client actually sends — a whole-state payload would converge
too (CRDT updates are idempotent) but would hide a broken delta.
"""
from __future__ import annotations

import os

import pytest
from pycrdt import Doc, XmlElement, XmlFragment, XmlText, handle_sync_message
from sqlalchemy import text

from app.auth import users as users_repo
from app.db.models import DocumentTemplate
from app.db.session import session as db_session
from app.db.session import try_advisory_xact_lock
from app.tasks import coedit_checkpoint as coedit_checkpoint_task
from app.tasks.queues import coedit_queue
from app.wiki import coedit, coedit_checkpoint, coedit_live, drafts, markdown_yjs
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


def _client(sess, body: str) -> Doc:
    """A client's document, seeded the way ``app/api/coedit.py:ws`` seeds a
    session's first connection — including the initial snapshot
    (``coedit.set_initial_snapshot``), without which a checkpoint has nothing
    to rebuild from (see ``coedit_checkpoint.checkpoint_session``'s guard).

    The ``Doc`` stays with the test, not the server: the test edits it and
    ships the deltas, exactly as a browser would."""
    doc = markdown_yjs.seed_doc_from_markdown(body)
    coedit.set_initial_snapshot(sess.id, doc.get_update(), body)
    return doc


def _edit(sess, client: Doc, uid: str, prefix: str) -> None:
    """Prepend ``prefix`` to the client's first paragraph and log the delta —
    the two steps the WS route performs on a real edit (integrate into the doc,
    log the update), driven directly here rather than through pycrdt's wire
    protocol."""
    before = client.get_state()
    root = _root(client)
    with client.transaction():
        root.children[0].children[0].insert(0, prefix)
    coedit.apply_update(sess.id, update_bytes=client.get_update(before), author_user_id=uid)


def _new_page(sess, uid: str, text_: str) -> Doc:
    """For a session with no base content (base_sha=None, page doesn't exist
    yet): the document starts with zero blocks, so there's no existing
    paragraph to prepend into — a whole new block has to be constructed
    directly, same pattern as
    test_markdown_splice.py's new-top-level-block test."""
    client = _client(sess, "")
    before = client.get_state()
    root = _root(client)
    para = XmlElement("paragraph", {"_blockId": "new0"}, contents=[])
    with client.transaction():
        root.children.append(para)
    with client.transaction():
        para.children.append(XmlText(text_))
    coedit.apply_update(sess.id, update_bytes=client.get_update(before), author_user_id=uid)
    return client


def test_checkpoint_commits_and_attributes_last_editor(repo):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    client = _client(sess, "hello world")
    _edit(sess, client, uid, "EDITED ")

    outcome = coedit_checkpoint.checkpoint_session(sess.id)

    assert outcome is not None
    # Trailing newline: the touched (only) block is fully re-serialized via
    # markdown_yjs.serialize_block, which always terminates a block with
    # exactly one newline — see that function's own docstring.
    assert wiki_git.read_file(_PATH) == "EDITED hello world\n"
    st = coedit.get_active_session(_PATH)
    assert st is not None
    assert st.ydoc_checkpointed_seq == 1
    assert st.base_sha == outcome.sha
    # git author is the last editor.
    assert wiki_git.history(_PATH)[0].author == "Ada"


def test_checkpoint_folds_in_late_update_landing_during_its_own_commit(repo, monkeypatch):
    """A coedit_updates row logged *during* checkpoint_session's own
    commit_and_fan_out call — after _rebuild_doc already replayed up to some
    seq, but before advance_checkpoint would persist past it — must not be
    silently discarded. The fix is in checkpoint_session itself (its fold-in
    retry loop), and the assertion that sees this class of bug is against
    committed git content."""
    uid_a = users_repo.create(email="alice@x.com", password="hunter2-x", name="Alice")
    uid_b = users_repo.create(email="bob@x.com", password="hunter2-x", name="Bob")
    body = "Block A original.\n\nBlock B original.\n"
    sha = _seed_page(body)
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid_a)
    coedit.join(sess.id, uid_b)
    client = _client(sess, body)
    _edit(sess, client, uid_a, "ALICE: ")

    from app.wiki import utils as wiki_utils

    real_commit_and_fan_out = wiki_utils.commit_and_fan_out
    calls = {"n": 0}

    def fake_commit_and_fan_out(*args, **kwargs):
        calls["n"] += 1
        result = real_commit_and_fan_out(*args, **kwargs)
        if calls["n"] == 1:
            # Bob's edit lands on a *different* block, durably logged,
            # while checkpoint_session's own first commit_and_fan_out call
            # is still "in flight" from its own perspective.
            before = client.get_state()
            root = _root(client)
            with client.transaction():
                root.children[2].children[0].insert(0, "BOB: ")
            coedit.apply_update(
                sess.id, update_bytes=client.get_update(before), author_user_id=uid_b
            )
        return result

    monkeypatch.setattr(wiki_utils, "commit_and_fan_out", fake_commit_and_fan_out)

    outcome = coedit_checkpoint.checkpoint_session(sess.id)

    assert outcome is not None
    assert calls["n"] == 2, f"expected one fold-in retry, got {calls['n']} commit attempts"
    committed = wiki_git.read_file(_PATH)
    assert "ALICE: Block A original." in committed
    assert "BOB: Block B original." in committed, (
        "bob's late-arriving edit was lost — the black hole bug is back"
    )
    st = coedit.get_active_session(_PATH)
    assert st is not None
    assert st.ydoc_checkpointed_seq == st.ydoc_seq, "session must be fully clean, not just partially"


def test_diverged_checkpoint_reaches_the_editors(repo):
    # When the checkpoint's 3-way merge folds in a concurrent agent commit, the
    # committed result differs from what the editors have on screen. They have
    # to receive it: the snapshot alone would only reach them on a reconnect,
    # and the next checkpoint would rebuild from a document that never saw the
    # agent's edit. It's broadcast as an ordinary Yjs update (the splice
    # preserves lineage, so a delta is expressible), which is also what lets a
    # client rebase its own pending edits over it.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    body = "one\ntwo\nthree\nfour\nfive\n"
    sha = _seed_page(body)
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    client = _client(sess, body)
    # Human edits inside the (single, soft-break-joined) paragraph; agent
    # commits a distant, non-overlapping change out of band, against the
    # original HEAD, unaware of the human's in-session edit.
    _edit(sess, client, uid, "EDIT-")
    wiki_git.commit_file(_PATH, "one\ntwo\nthree\nfour\nFIVE\n", "agent edit", author="Agent <a@x.com>")
    sent: list[bytes] = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            coedit_checkpoint.coedit_channel,
            "broadcast_yjs",
            lambda session_id, payload, seq=None: sent.append(payload),
        )
        outcome = coedit_checkpoint.checkpoint_session(sess.id)

    merged = "EDIT-one\ntwo\nthree\nfour\nFIVE\n"
    assert outcome is not None and outcome.diverged is True
    assert wiki_git.read_file(_PATH) == merged
    st = coedit.get_active_session(_PATH)
    assert st is not None
    assert st.ydoc_checkpointed_seq == st.ydoc_seq  # clean

    # One broadcast, and applying it to the client converges it on the merged
    # result — a resync frame is no longer needed for this.
    assert len(sent) == 1
    handle_sync_message(sent[0][1:], client)
    assert markdown_yjs.reconstruct_body(client) == merged

    # And the durable state agrees, so a client that missed the broadcast gets
    # the same thing from its next handshake.
    assert coedit_live.read_body(sess.id) == merged

    # A second checkpoint is a clean no-op — the agent's edit is retained, not
    # dropped by re-committing a stale document.
    assert coedit_checkpoint.checkpoint_session(sess.id) is None
    assert wiki_git.read_file(_PATH) == merged


def test_late_update_after_a_diverged_checkpoint_is_not_pruned(repo):
    # A keystroke logged during the checkpoint's git-commit-plus-merge window,
    # past the engine's fold-in bound: advance_checkpoint prunes only up to the
    # seq the checkpoint actually captured, so the row survives and the session
    # is left dirty for the next checkpoint to pick up rather than sealed clean
    # with the edit stranded.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    body = "one\ntwo\nthree\nfour\nfive\n"
    sha = _seed_page(body)
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    client = _client(sess, body)
    _edit(sess, client, uid, "EDIT-")
    wiki_git.commit_file(_PATH, "one\ntwo\nthree\nfour\nFIVE\n", "agent edit", author="Agent <a@x.com>")

    outcome = coedit_checkpoint.checkpoint_session(sess.id)
    assert outcome is not None
    assert outcome.diverged is True
    checkpointed = coedit.get_active_session(sess.path)
    assert checkpointed is not None

    _edit(sess, client, uid, "LATE-")

    st = coedit.get_active_session(_PATH)
    assert st is not None
    assert st.ydoc_seq > st.ydoc_checkpointed_seq  # dirty again → will be re-checkpointed
    # The row itself is still in the log, above the checkpoint watermark.
    late = coedit.updates_since(sess.id, checkpointed.ydoc_checkpointed_seq)
    assert [u.seq for u in late.updates] == [st.ydoc_seq]


def test_checkpoint_survives_a_racing_close(repo, monkeypatch):
    # If the session's final bookkeeping write races a concurrent close
    # (advance_checkpoint's conditional UPDATE matches zero rows), the
    # checkpoint must not raise — the git commit already landed and is the
    # real source of truth; the session is dead either way, so its DB row
    # not advancing further is harmless.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    client = _client(sess, "hello world")
    _edit(sess, client, uid, "EDITED ")

    monkeypatch.setattr(coedit, "advance_checkpoint", lambda *a, **k: None)
    outcome = coedit_checkpoint.checkpoint_session(sess.id)

    assert outcome is not None
    assert wiki_git.read_file(_PATH) == "EDITED hello world\n"


def test_checkpoint_is_noop_when_clean(repo):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    client = _client(sess, "hello world")
    _edit(sess, client, uid, "EDITED ")
    assert coedit_checkpoint.checkpoint_session(sess.id) is not None
    # Nothing new since the last checkpoint → no second commit.
    assert coedit_checkpoint.checkpoint_session(sess.id) is None


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
    client = _client(sess, tmpl_body)
    _edit(sess, client, uid, "Mine: ")

    coedit_checkpoint.checkpoint_session(sess.id)

    assert drafts.get(_PATH) is None  # diverged from the template → row cleared


def test_task_checkpoints_then_closes_when_empty(repo):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    client = _client(sess, "hello world")
    _edit(sess, client, uid, "EDITED ")
    coedit.leave(sess.id, uid)  # last participant gone

    # checkpoint_coedit_session_task is @coedit_queue.task()-decorated —
    # calling it directly enqueues rather than running (see Task.__call__);
    # immediate_mode runs the handler synchronously so the assertions below
    # see its effects.
    with coedit_queue.immediate_mode():
        coedit_checkpoint_task.checkpoint_coedit_session_task(sess.id)

    assert wiki_git.read_file(_PATH) == "EDITED hello world\n"
    # No participants remained → the task closed the session.
    assert coedit.get_active_session(_PATH) is None


def test_task_keeps_session_open_when_participants_remain(repo):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    client = _client(sess, "hello world")
    _edit(sess, client, uid, "EDITED ")

    with coedit_queue.immediate_mode():
        coedit_checkpoint_task.checkpoint_coedit_session_task(sess.id)

    assert wiki_git.read_file(_PATH) == "EDITED hello world\n"
    # A participant is still editing → session stays active.
    st = coedit.get_active_session(_PATH)
    assert st is not None
    assert st.ydoc_checkpointed_seq == 1


def test_scan_checkpoints_due_sessions(repo, monkeypatch):
    monkeypatch.setattr(coedit_checkpoint_task, "_IDLE_SECONDS", 0)
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    client = _client(sess, "hello world")
    _edit(sess, client, uid, "EDITED ")
    coedit.leave(sess.id, uid)

    with coedit_queue.immediate_mode():
        coedit_checkpoint_task.scan_coedit_checkpoints()

    assert wiki_git.read_file(_PATH) == "EDITED hello world\n"
    assert coedit.get_active_session(_PATH) is None


def test_scan_checkpoints_a_session_no_process_holds(repo, monkeypatch):
    # A dirty session whose editors are all connected to some other process —
    # or gone entirely — is still this scan's to checkpoint. Nothing about the
    # work is process-local: the throwaway Doc comes from (snapshot, updates).
    # The client Doc here is dropped immediately after logging its edit, which
    # is the state a departed editor leaves behind.
    monkeypatch.setattr(coedit_checkpoint_task, "_IDLE_SECONDS", 0)
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    _edit(sess, _client(sess, "hello world"), uid, "EDITED ")

    with coedit_queue.immediate_mode():
        coedit_checkpoint_task.scan_coedit_checkpoints()

    assert wiki_git.read_file(_PATH) == "EDITED hello world\n"
    st = coedit.get_active_session(_PATH)
    assert st is not None
    assert st.ydoc_seq == st.ydoc_checkpointed_seq  # clean


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
    # it would clobber newer HEAD with stale content. No snapshot needed: the
    # closed+dirty check short-circuits before the engine tries to rebuild a
    # doc.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    # Never replayed — checkpoint_session bails on the closed+dirty check
    # before it ever looks at the update log, so this doesn't need to be a
    # real, applicable Yjs update (unlike _edit's payloads).
    coedit.apply_update(sess.id, update_bytes=b"x", author_user_id=uid)
    # An agent lands a newer commit after the session's base.
    newer = wiki_git.commit_file(_PATH, "hello world v2", "agent", author="A <a@x.com>")
    coedit.close_session(sess.id)  # closed while still dirty

    assert coedit_checkpoint.checkpoint_session(sess.id) is None  # skipped
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
    client = _client(sess, "hello world")
    _edit(sess, client, uid, "EDITED ")
    coedit.leave(sess.id, uid)  # last participant gone → eligible to close

    before = len(wiki_git.history(_PATH))

    with coedit_queue.immediate_mode():
        coedit_checkpoint_task.checkpoint_coedit_session_task(sess.id)  # commits + closes
        coedit_checkpoint_task.checkpoint_coedit_session_task(sess.id)  # duplicate → no-op
        coedit_checkpoint_task.checkpoint_coedit_session_task(sess.id)  # duplicate → no-op

    after = len(wiki_git.history(_PATH))
    assert after - before == 1  # exactly one "Co-editing checkpoint" commit
    assert wiki_git.read_file(_PATH) == "EDITED hello world\n"


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
    client = _client(sess, "hello world")
    _edit(sess, client, uid, "HOWDY ")

    new_path = "guides/install.md"
    wiki_git.move_path(_PATH, new_path, "rename")
    coedit.on_path_moved([PathMove(old=_PATH, new=new_path)])

    outcome = coedit_checkpoint.checkpoint_session(sess.id)
    assert outcome is not None
    # The doc landed on the page's new home; the old path stays gone.
    assert wiki_git.read_file_opt(new_path) == "HOWDY hello world\n"
    assert wiki_git.read_file_opt(_PATH) is None


def test_checkpoint_closes_session_when_path_gone(repo):
    uid = users_repo.create(email="zoe@x.com", password="hunter2-x", name="Zoe")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    client = _client(sess, "hello world")
    _edit(sess, client, uid, "STALE ")

    # The page moves away but the session is NOT re-keyed (the pre-fix bug,
    # or a raw-git rename that bypassed the lifecycle hook). The editor
    # left, so the session is participant-less — the zombie case the guard
    # closes.
    coedit.leave(sess.id, uid)
    wiki_git.move_path(_PATH, "guides/renamed.md", "rename")

    assert coedit_checkpoint.checkpoint_session(sess.id) is None
    # No resurrection at the dead path, and the session is closed. (Unlike
    # the OT era, nothing persists the lost edit for manual recovery beyond
    # the update log's own bounded lifetime — see coedit.py's module
    # docstring.)
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

    assert coedit_checkpoint.checkpoint_session(sess.id) is not None
    # Trailing newline: a brand-new block is fully serialized via
    # markdown_yjs.serialize_block, which always terminates with one.
    assert wiki_git.read_file_opt("guides/fresh.md") == "first words\n"


def test_checkpoint_skips_but_keeps_session_with_participants(repo):
    uid = users_repo.create(email="liv@x.com", password="hunter2-x", name="Liv")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    client = _client(sess, "hello world")
    _edit(sess, client, uid, "LIVE ")

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
    # Never replayed — this test only calls on_path_moved, no checkpoint.
    coedit.apply_update(young.id, update_bytes=b"few chars", author_user_id=uid)

    superseded_ids = coedit.on_path_moved([PathMove(old=_PATH, new="guides/target.md")])

    # The origin session follows the page; the young session closes.
    at_dest = coedit.get_active_session("guides/target.md")
    assert at_dest is not None and at_dest.id == origin.id
    superseded = coedit.get_session(young.id)
    assert superseded is not None
    assert superseded.status == coedit.SessionStatus.CLOSED.value
    # Returned so a caller can act on the superseded session (nothing in-memory
    # to evict any more — see on_path_moved's own docstring).
    assert superseded_ids == [young.id]


def test_on_path_moved_supersedes_clean_destination_session(repo):
    uid = users_repo.create(email="ed@x.com", password="hunter2-x", name="Ed")
    sha = _seed_page("origin body")
    origin = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(origin.id, uid)
    # Never replayed — this test only calls on_path_moved, no checkpoint.
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


def test_implausible_growth_guard() -> None:
    """The backstop for any route to a doubled document the lineage rule
    doesn't cover: a couple of update rows can add a paragraph, not a second
    copy of the page."""
    from app.wiki.coedit_checkpoint import _implausible_growth

    page = "x" * 8000
    assert _implausible_growth(page, page * 2, updates=1)
    assert _implausible_growth(page, page * 2, updates=3)

    # Ordinary editing is untouched: a big append over many updates, a small
    # page (a paste can legitimately dwarf it), and normal growth all pass.
    assert not _implausible_growth(page, page * 2, updates=4)
    assert not _implausible_growth("tiny", "tiny" * 50, updates=1)
    assert not _implausible_growth(page, page + "a new paragraph", updates=1)
    assert not _implausible_growth(page, page[:100], updates=1)
