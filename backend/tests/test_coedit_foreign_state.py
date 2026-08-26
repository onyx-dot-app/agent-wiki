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
import time

import pytest
from fastapi.testclient import TestClient
from pycrdt import Doc, YMessageType, create_sync_message, create_update_message, handle_sync_message

from app.auth import users as users_repo
from app.main import create_app
from app.wiki import coedit, coedit_live, doc_ids, markdown_yjs
from app.wiki import git as wiki_git
from tests._auth import login_fastapi

_PATH = "guides/foreign.md"
_BODY = "alpha one\n\nbeta two\n"


@pytest.fixture
def client(tmp_db, tmp_repo):
    return TestClient(create_app())


def _seed_session(body: str) -> tuple[coedit.SessionRow, Doc]:
    sha = wiki_git.commit_file(_PATH, body, "seed", author="Seed <seed@x.com>")
    # Raw git seeding bypasses the lifecycle hooks; mint the registry id they
    # would have, so the document-row mirror resolves the path.
    doc_ids.mint_for_page(_PATH)
    sess = coedit.open_session(_PATH, base_sha=sha)
    doc = markdown_yjs.seed_doc_from_markdown(body)
    coedit.set_initial_snapshot(sess.id, doc.get_update(), body)
    return sess, doc


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


def test_foreign_step1_is_flagged_and_reply_still_computed(tmp_repo):
    sess, _server_doc = _seed_session(_BODY)
    foreign_doc = markdown_yjs.seed_doc_from_markdown("POISON copy of the page\n")
    reply, foreign = coedit_live.sync_reply(sess.id, create_sync_message(foreign_doc))
    assert foreign
    # sync_reply reports; withholding the reply is the route's decision.
    assert reply is not None


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

        # Its content is dropped: nothing logged, nothing in the body.
        ws.send_bytes(create_update_message(foreign_doc.get_update()))
        time.sleep(0.3)
        after = coedit.get_session(session_id)
        assert after is not None and after.ydoc_seq == before.ydoc_seq
        body = coedit_live.read_body(session_id)
        assert body is not None and "POISON" not in body

        # Suppress the teardown checkpoint noise on context exit.
        with contextlib.suppress(Exception):
            ws.close()
