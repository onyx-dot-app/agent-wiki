"""The AgentWikiEditor Yjs WebSocket transport — app/api/coedit.py +
app/wiki/coedit_ws.py end to end: connect, sync, edit, disconnect, confirm
the edit lands in git via the targeted-splice checkpoint with untouched
regions preserved byte-for-byte, and the per-update write-permission
re-check invariant (PR #489) applied to the Yjs relay.

Uses ``with TestClient(app) as client:`` (entering the app lifespan) rather
than the bare ``TestClient(create_app())`` pattern used by the DB-only
coedit test files — the WS endpoint needs ``coedit_ws.SERVER`` actually
started, which only happens inside the lifespan context.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from pycrdt import Doc, XmlFragment, create_sync_message, create_update_message, handle_sync_message

from app.auth import users as users_repo
from app.main import create_app
from app.wiki import acl, coedit, git as wiki_git
from app.wiki.markdown_yjs import ROOT_XML_KEY

from tests._auth import login_fastapi

_PATH = "guides/coedit-ws-setup.md"
_SEED_BODY = "# Setup\n\nOriginal paragraph text.\n\nAnother paragraph, untouched.\n"

# The last-leave checkpoint runs as an independent background task (see
# api/coedit.py's module note on why it can't be awaited inline in the
# connection handler), so tests poll briefly rather than assuming it lands
# the instant the `with websocket_connect` block exits.
_CHECKPOINT_POLL_SECONDS = 3.0
_CHECKPOINT_POLL_INTERVAL = 0.1


@pytest.fixture
def client(tmp_db, tmp_repo):
    with TestClient(create_app()) as c:
        yield c


def _sync_client_doc(ws) -> Doc:
    """Perform the Yjs SYNC_STEP1/STEP2 handshake a real client would do,
    against a fresh local Doc. Returns the synced Doc."""
    local_doc = Doc()
    ws.send_bytes(bytes(create_sync_message(local_doc)))
    for _ in range(2):
        msg = ws.receive_bytes()
        reply = handle_sync_message(msg[1:], local_doc)
        if reply is not None:
            ws.send_bytes(bytes(reply))
    return local_doc


def _wait_for_body_containing(
    path: str, needle: str, timeout: float = _CHECKPOINT_POLL_SECONDS
) -> str:
    deadline = time.monotonic() + timeout
    body = wiki_git.read_file_opt(path) or ""
    while needle not in body and time.monotonic() < deadline:
        time.sleep(_CHECKPOINT_POLL_INTERVAL)
        body = wiki_git.read_file_opt(path) or ""
    return body


def test_ws_requires_auth(client):
    with pytest.raises(Exception):
        with client.websocket_connect(f"/api/coedit/ws/{_PATH}"):
            pass


def test_ws_requires_read_permission(client):
    owner = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    other = users_repo.create(email="other@x.com", password="hunter2-x", name="Other")
    wiki_git.commit_file(_PATH, _SEED_BODY, message="seed", author="Owner <owner@x.com>")
    acl.set_owner(_PATH, owner)  # owner-only page, no grant to `other`

    login_fastapi(client, other)
    with pytest.raises(Exception):
        with client.websocket_connect(f"/api/coedit/ws/{_PATH}"):
            pass


def test_edit_over_ws_checkpoints_to_git_with_untouched_regions_preserved(client):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    wiki_git.commit_file(_PATH, _SEED_BODY, message="seed", author="Ada <ada@x.com>")

    with client.websocket_connect(f"/api/coedit/ws/{_PATH}") as ws:
        local_doc = _sync_client_doc(ws)
        root = local_doc.get(ROOT_XML_KEY, type=XmlFragment)
        target = next(c for c in root.children if c.tag == "paragraph")
        with local_doc.transaction():
            target.children[0].insert(0, "EDITED: ")

        update_bytes = local_doc.get_update(b"\x00")
        ws.send_bytes(bytes(create_update_message(update_bytes)))
        time.sleep(0.2)  # let the server apply + queue the persist write

    new_body = _wait_for_body_containing(_PATH, "EDITED: Original paragraph text.")

    assert "EDITED: Original paragraph text." in new_body
    # Untouched regions survive byte-for-byte, including the surrounding
    # heading and the second paragraph — this is the whole point of the
    # targeted-splice checkpoint engine (markdown_splice.py).
    assert new_body.startswith("# Setup\n\n")
    assert new_body.endswith("Another paragraph, untouched.\n")


def test_two_peers_converge_and_both_edits_land(client):
    """Two clients connect to the same page, each edits a different
    paragraph, and both edits land in the committed body — the WS-layer
    counterpart to test_markdown_splice.py's pure-Doc convergence test."""
    uid = users_repo.create(email="ada2@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    path = _PATH + ".two"
    wiki_git.commit_file(path, _SEED_BODY, message="seed", author="Ada <ada2@x.com>")

    with client.websocket_connect(f"/api/coedit/ws/{path}") as ws_a:
        doc_a = _sync_client_doc(ws_a)
        with client.websocket_connect(f"/api/coedit/ws/{path}") as ws_b:
            doc_b = _sync_client_doc(ws_b)

            root_a = doc_a.get(ROOT_XML_KEY, type=XmlFragment)
            first_para = next(c for c in root_a.children if c.tag == "paragraph")
            with doc_a.transaction():
                first_para.children[0].insert(0, "FROM A: ")
            ws_a.send_bytes(bytes(create_update_message(doc_a.get_update(b"\x00"))))

            root_b = doc_b.get(ROOT_XML_KEY, type=XmlFragment)
            paragraphs_b = [c for c in root_b.children if c.tag == "paragraph"]
            with doc_b.transaction():
                paragraphs_b[-1].children[0].insert(0, "FROM B: ")
            ws_b.send_bytes(bytes(create_update_message(doc_b.get_update(b"\x00"))))

            time.sleep(0.3)

    new_body = _wait_for_body_containing(path, "FROM B: Another paragraph")
    assert "FROM A: Original paragraph text." in new_body
    assert "FROM B: Another paragraph, untouched." in new_body
    assert new_body.startswith("# Setup\n\n")


def test_write_permission_revoked_mid_session_closes_connection(client):
    # The invariant PR #489 established for the OT-era transport, applied
    # here for the first time to the Yjs relay: write permission is
    # re-checked on every mutating frame, not just at connect — a mid-
    # session ACL revocation takes effect immediately, not on next reconnect.
    owner = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    editor = users_repo.create(email="editor@x.com", password="hunter2-x", name="Editor")
    wiki_git.commit_file(_PATH, _SEED_BODY, message="seed", author="Owner <owner@x.com>")
    acl.set_owner(_PATH, owner)
    entry_id = acl.grant(
        resource_kind="page",
        resource_path=_PATH,
        principal_kind="user",
        principal_id=editor,
        permission="write",
        granted_by_user_id=owner,
    )

    login_fastapi(client, editor)
    with client.websocket_connect(f"/api/coedit/ws/{_PATH}") as ws:
        local_doc = _sync_client_doc(ws)
        root = local_doc.get(ROOT_XML_KEY, type=XmlFragment)
        target = next(c for c in root.children if c.tag == "paragraph")

        # First edit succeeds — still has write.
        with local_doc.transaction():
            target.children[0].insert(0, "FIRST: ")
        ws.send_bytes(bytes(create_update_message(local_doc.get_update(b"\x00"))))
        time.sleep(0.2)

        acl.revoke(entry_id)

        # Second edit is rejected — the connection is closed with 1008
        # instead of the update being applied.
        with local_doc.transaction():
            target.children[0].insert(0, "SECOND: ")
        ws.send_bytes(bytes(create_update_message(local_doc.get_update(b"\x00"))))

        with pytest.raises(Exception):
            for _ in range(5):
                ws.receive_bytes()

    body = _wait_for_body_containing(_PATH, "FIRST: Original paragraph text.")
    assert "FIRST: Original paragraph text." in body
    assert "SECOND: " not in body


def test_read_only_viewer_can_connect_but_cannot_write(client):
    owner = users_repo.create(email="owner2@x.com", password="hunter2-x", name="Owner")
    viewer = users_repo.create(email="viewer@x.com", password="hunter2-x", name="Viewer")
    path = _PATH + ".viewer"
    wiki_git.commit_file(path, _SEED_BODY, message="seed", author="Owner <owner2@x.com>")
    acl.set_owner(path, owner)
    acl.grant(
        resource_kind="page",
        resource_path=path,
        principal_kind="user",
        principal_id=viewer,
        permission="read",
        granted_by_user_id=owner,
    )

    login_fastapi(client, viewer)
    with client.websocket_connect(f"/api/coedit/ws/{path}") as ws:
        # Connecting and syncing (a read) is allowed.
        local_doc = _sync_client_doc(ws)
        root = local_doc.get(ROOT_XML_KEY, type=XmlFragment)
        target = next(c for c in root.children if c.tag == "paragraph")

        with local_doc.transaction():
            target.children[0].insert(0, "SHOULD NOT LAND: ")
        ws.send_bytes(bytes(create_update_message(local_doc.get_update(b"\x00"))))

        with pytest.raises(Exception):
            for _ in range(5):
                ws.receive_bytes()

    body = wiki_git.read_file_opt(path) or ""
    assert "SHOULD NOT LAND" not in body


def test_session_joins_participant(client):
    uid = users_repo.create(email="joiner@x.com", password="hunter2-x", name="Joiner")
    login_fastapi(client, uid)
    path = _PATH + ".join"
    wiki_git.commit_file(path, _SEED_BODY, message="seed", author="Joiner <joiner@x.com>")

    with client.websocket_connect(f"/api/coedit/ws/{path}") as ws:
        _sync_client_doc(ws)
        sess = coedit.get_active_session(path)
        assert sess is not None
        participants = coedit.list_participants(sess.id)
        assert [p.user_id for p in participants] == [uid]
