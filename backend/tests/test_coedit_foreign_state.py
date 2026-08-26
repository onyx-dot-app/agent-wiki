"""Foreign-state guard — the backstop for clients that predate
``resync_required``.

The lineage-generation guard (``ydoc_lineage``) refuses stale updates from a
connection that joined before a reseed. It cannot catch a stale client that
*reconnects after* the reseed: the new connection joins at the current
generation, and the poison is in the sync payload, not the counter. The
foreign-state guard closes that: a SYNC_STEP1 whose state vector shares no
client id with the session's document marks the connection foreign — its
content frames are dropped and no sync reply feeds it more content to union.
"""

from __future__ import annotations

import contextlib
import json

import pytest
from fastapi.testclient import TestClient
from pycrdt import Doc, YMessageType, create_sync_message, create_update_message, handle_sync_message

from app.auth import users as users_repo
from app.main import create_app
from app.tasks.queues import coedit_queue
from app.wiki import coedit, coedit_live, doc_ids, markdown_yjs
from app.wiki import git as wiki_git
from tests._auth import login_fastapi

_PATH = "guides/foreign.md"
_BODY = "alpha one\n\nbeta two\n"


@pytest.fixture
def client(tmp_db, tmp_repo):
    # Immediate mode makes the WS checkpoint request synchronous, so the
    # end-to-end test's checkpoint-ack barrier resolves without a worker.
    with coedit_queue.immediate_mode():
        yield TestClient(create_app())


def _seed_session(body: str) -> tuple[coedit.SessionRow, Doc]:
    sha = wiki_git.commit_file(_PATH, body, "seed", author="Seed <seed@x.com>")
    # Raw git seeding bypasses the lifecycle hooks; mint the registry id they
    # would have, so the document-row mirror resolves the path.
    doc_ids.mint_for_page(_PATH)
    sess = coedit.open_session(_PATH, base_sha=sha)
    doc = markdown_yjs.seed_doc_from_markdown(body)
    coedit.set_initial_snapshot(sess.id, doc.get_update(), body)
    return sess, doc


def _log_edit(sess: coedit.SessionRow, doc: Doc, prefix: str) -> None:
    """Prepend ``prefix`` to the doc's first paragraph and log the delta —
    a real local edit, so a checkpoint has something to commit."""
    from pycrdt import XmlFragment

    from app.wiki.markdown_yjs import ROOT_XML_KEY

    before = doc.get_state()
    root = doc.get(ROOT_XML_KEY, type=XmlFragment)
    with doc.transaction():
        root.children[0].children[0].insert(0, prefix)
    coedit.apply_update(sess.id, update_bytes=doc.get_update(before), author_user_id=None)


def _reseed(sess: coedit.SessionRow, doc: Doc, monkeypatch) -> None:
    """Drive the checkpoint engine down its reseed-on-divergence branch: a
    local edit plus an out-of-band commit diverge the merge, and a patched
    splice forces the lineage-discarding fallback."""
    import app.wiki.markdown_splice as markdown_splice
    from app.wiki import coedit_checkpoint

    _log_edit(sess, doc, "EDIT ")
    wiki_git.commit_file(_PATH, "alpha one\n\nbeta CHANGED\n", "oob", author="X <x@x.com>")
    # Restore the attribute explicitly — monkeypatch.undo() would also wipe
    # the conftest's CONFIG patches, which share this monkeypatch instance.
    orig = markdown_splice.apply_markdown_diff
    monkeypatch.setattr(markdown_splice, "apply_markdown_diff", lambda *a, **k: False)
    assert coedit_checkpoint.checkpoint_session(sess.id) is not None
    monkeypatch.setattr(markdown_splice, "apply_markdown_diff", orig)


def test_state_vector_client_ids_decodes_the_docs_own_ids():
    doc = markdown_yjs.seed_doc_from_markdown("hi\n")
    assert coedit_live.state_vector_client_ids(doc.get_state()) == {doc.client_id}
    assert coedit_live.state_vector_client_ids(Doc().get_state()) == set()


def test_same_lineage_step1_is_not_foreign(tmp_repo):
    sess, server_doc = _seed_session(_BODY)
    client = Doc()
    client.apply_update(server_doc.get_update())  # synced: shares the lineage
    _reply, foreign = coedit_live.sync_reply(sess.id, create_sync_message(client))
    assert not foreign


def test_empty_client_step1_is_not_foreign(tmp_repo):
    sess, _server_doc = _seed_session(_BODY)
    _reply, foreign = coedit_live.sync_reply(sess.id, create_sync_message(Doc()))
    assert not foreign


def test_mixed_lineage_client_is_flagged_via_retired_ids(tmp_repo, monkeypatch):
    """The incident shape: a stale tab keeps integrating current-lineage
    broadcasts after a reseed, so its state vector holds BOTH lineages' ids —
    overlap with the current document proves nothing. The retired-id test is
    what catches it."""
    sess, old_doc = _seed_session(_BODY)
    _reseed(sess, old_doc, monkeypatch)

    refreshed = coedit.get_session_for_checkpoint(sess.id)
    assert refreshed is not None and refreshed.ydoc_snapshot is not None
    # The replaced lineage's ids were retired at the reseed.
    assert old_doc.client_id in refreshed.retired_client_ids

    # The stale tab: old doc that has ALSO integrated the current lineage.
    stale = Doc()
    stale.apply_update(old_doc.get_update())
    stale.apply_update(refreshed.ydoc_snapshot)
    # Sanity: it genuinely overlaps the current document's ids — the
    # any-overlap rule alone would have waved it through.
    server_now = Doc()
    server_now.apply_update(refreshed.ydoc_snapshot)
    stale_ids = coedit_live.state_vector_client_ids(stale.get_state())
    assert stale_ids & coedit_live.state_vector_client_ids(server_now.get_state())
    _reply, foreign = coedit_live.sync_reply(sess.id, create_sync_message(stale))
    assert foreign, "mixed-lineage doc must be refused despite current-id overlap"

    # A purely current client stays welcome.
    current = Doc()
    current.apply_update(refreshed.ydoc_snapshot)
    _reply, foreign = coedit_live.sync_reply(sess.id, create_sync_message(current))
    assert not foreign


def test_new_session_inherits_retired_ids(tmp_repo, monkeypatch):
    """Retired ids survive session turnover via the document-row mirror, so a
    stale tab can't dodge the guard by rejoining a brand-new session."""
    sess, old_doc = _seed_session(_BODY)
    _reseed(sess, old_doc, monkeypatch)
    coedit.close_session(sess.id)

    sess2 = coedit.open_session(_PATH, base_sha=wiki_git.head_sha_for_path(_PATH))
    assert sess2.id != sess.id
    attached, _base = coedit.transplant_from_document(sess2.id, _PATH)
    assert attached
    row = coedit.get_session_for_checkpoint(sess2.id)
    assert row is not None and old_doc.client_id in row.retired_client_ids

    _reply, foreign = coedit_live.sync_reply(sess2.id, create_sync_message(old_doc))
    assert foreign


def test_straggler_ids_are_retired_after_the_bump(tmp_repo, monkeypatch):
    """A client's first-ever update logged in the reseed's pre-bump window
    carries an id the discarded doc's state vector never saw. The post-bump
    second pass must retire it, or that client's later mixed state vector
    would slip past both foreign tests."""
    from app.db.models import CoeditUpdate
    from app.db.session import session as db_session
    from app.wiki import coedit_checkpoint

    sess, old_doc = _seed_session(_BODY)
    _reseed(sess, old_doc, monkeypatch)

    # Simulate the pre-bump straggler: an old-generation row (lineage 0) that
    # survived the reseed's prune — exactly what a commit racing the bump
    # leaves behind (apply_update would refuse it now, so insert directly).
    straggler = markdown_yjs.seed_doc_from_markdown("STRAY typed blind\n")
    row = coedit.get_session_for_checkpoint(sess.id)
    assert row is not None
    with db_session() as s:
        s.add(
            CoeditUpdate(
                session_id=sess.id,
                seq=row.ydoc_seq + 1000,
                author_user_id=None,
                client_id=None,
                lineage=0,
                update_payload=straggler.get_update(),
            )
        )
    assert straggler.client_id not in row.retired_client_ids

    # The second pass folds the straggler into the discarded doc and retires
    # its id (old_doc's own state was already retired at the bump).
    replay = Doc()
    assert row.ydoc_snapshot is not None
    replay.apply_update(old_doc.get_update())
    coedit_checkpoint._retire_straggler_ids(sess.id, replay)

    after = coedit.get_session_for_checkpoint(sess.id)
    assert after is not None and straggler.client_id in after.retired_client_ids

    # A mixed doc built from the straggler + current content is now foreign.
    mixed = Doc()
    mixed.apply_update(straggler.get_update())
    mixed.apply_update(after.ydoc_snapshot or b"")
    _reply, foreign = coedit_live.sync_reply(sess.id, create_sync_message(mixed))
    assert foreign


def test_suppress_yjs_drains_queued_binary_frames(tmp_repo):
    """Suppression must cover frames enqueued BEFORE the guard tripped — a
    broadcast racing the foreign STEP1 would otherwise still be delivered."""
    from app.wiki import coedit_channel

    sess, _doc = _seed_session(_BODY)
    conn = coedit_channel.connect(sess.id, lambda: None)
    try:
        coedit_channel.broadcast_yjs(sess.id, b"\x00pre-suppression frame")
        assert conn.queue.qsize() == 1
        coedit_channel.suppress_yjs(conn.id)
        assert conn.queue.qsize() == 0  # queued binary drained
        coedit_channel.broadcast_yjs(sess.id, b"\x00post-suppression frame")
        assert conn.queue.qsize() == 0  # new binary withheld
        coedit_channel.publish_control(sess.id, {"type": "resync_required", "reason": "t"})
        assert conn.queue.qsize() == 1  # control frames still flow
    finally:
        coedit_channel.disconnect(conn.id)


def test_foreign_step1_is_flagged_and_reply_withheld(tmp_repo):
    sess, _server_doc = _seed_session(_BODY)
    foreign_doc = markdown_yjs.seed_doc_from_markdown("POISON copy of the page\n")
    reply, foreign = coedit_live.sync_reply(sess.id, create_sync_message(foreign_doc))
    assert foreign
    # No reply for a foreign doc: STEP1's answer is essentially the whole
    # document, which would only feed the client-side union.
    assert reply is None


def test_malformed_state_vector_is_not_a_dos(tmp_repo):
    """A crafted varint run must fail fast (bounded decode), and a malformed
    state vector must not crash the verdict — it falls through to pycrdt."""
    import pytest as _pytest

    with _pytest.raises(ValueError):
        coedit_live.state_vector_client_ids(b"\xff" * 64)  # over-long varint
    with _pytest.raises(ValueError):
        coedit_live.state_vector_client_ids(b"\x80")  # truncated
    with _pytest.raises(ValueError):
        coedit_live.state_vector_client_ids(b"\x7f")  # count exceeds payload


def test_foreign_client_refused_end_to_end(client):
    """A stale tab reconnecting after a reseed: joins cleanly at the current
    generation, then offers a foreign state vector — the server must answer
    with ``resync_required`` and drop its content, not union it in."""
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    wiki_git.commit_file(_PATH, _BODY, "seed", author="Seed <seed@x.com>")

    with client.websocket_connect(f"/api/coedit/ws?path={_PATH}") as ws:
        joined = ws.receive_json()
        assert joined["type"] == "joined"
        ws.receive_bytes()  # the server's own SYNC_STEP1 query
        # Complete a normal empty-doc handshake first — proves a fresh client
        # is untouched by the guard.
        doc = Doc()
        ws.send_bytes(create_sync_message(doc))
        reply = ws.receive_bytes()
        assert reply[0] == YMessageType.SYNC
        handle_sync_message(reply[1:], doc)

        session_id = joined["session_id"]
        before = coedit.get_session(session_id)
        assert before is not None

        # Now the stale tab's offer: an independently seeded doc.
        foreign_doc = markdown_yjs.seed_doc_from_markdown("POISON copy of the page\n")
        ws.send_bytes(create_sync_message(foreign_doc))
        for _ in range(10):
            msg = ws.receive()
            text = msg.get("text")
            if text is None:
                continue  # skip presence/binary noise
            frame = json.loads(text)
            if frame.get("type") == "resync_required":
                assert frame["reason"] == "foreign_state"
                break
        else:
            raise AssertionError("never received resync_required")

        # Its content is dropped: nothing logged, nothing in the body. The
        # checkpoint request after it is the barrier — per-connection frames
        # process in order, so its ack proves the poison frame was handled
        # (a sleep would false-pass under CI load with the frame still
        # queued).
        ws.send_bytes(create_update_message(foreign_doc.get_update()))
        ws.send_json({"type": "checkpoint", "request_id": "barrier-1"})
        for _ in range(20):
            msg = ws.receive()
            text = msg.get("text")
            if text is None:
                continue
            frame = json.loads(text)
            if frame.get("type") == "checkpoint_result":
                assert frame["request_id"] == "barrier-1"
                break
        else:
            raise AssertionError("never received the checkpoint barrier ack")
        after = coedit.get_session(session_id)
        assert after is not None and after.ydoc_seq == before.ydoc_seq
        body = coedit_live.read_body(session_id)
        assert body is not None and "POISON" not in body

        # Suppress the teardown checkpoint noise on context exit.
        with contextlib.suppress(Exception):
            ws.close()
