"""The onyx-editor Yjs WebSocket transport (Phase 1, plans/onyx-editor.md) —
app/api/coedit_ws.py + app/wiki/coedit_ws.py end to end: connect, sync,
edit, disconnect, and confirm the edit lands in git via the targeted-splice
checkpoint with untouched regions preserved byte-for-byte.

Unlike the sibling coedit test files, this uses ``with TestClient(app) as
client:`` (entering the app lifespan) rather than the bare
``TestClient(create_app())`` pattern — the WS endpoint needs
``coedit_ws.SERVER`` actually started (``app/main.py``'s lifespan owns that),
which only happens inside the lifespan context. The extra lifespan work
(wiki seeding, template seeding, etc.) is harmless overhead against the
tmp_db/tmp_repo-provisioned fresh schema/wiki dir, just slower than the
other coedit test files.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from pycrdt import Doc, XmlFragment, create_sync_message, create_update_message, handle_sync_message

from app.auth import users as users_repo
from app.main import create_app
from app.wiki import git as wiki_git
from app.wiki.markdown_yjs import ROOT_XML_KEY

from tests._auth import login_fastapi

_PATH = "guides/coedit-ws-setup.md"
_SEED_BODY = "# Setup\n\nOriginal paragraph text.\n\nAnother paragraph, untouched.\n"

# Generous but bounded: the last-leave checkpoint runs as an independent
# background task (see api/coedit_ws.py's module note on why it can't be
# awaited inline in the connection handler), so the test polls briefly
# rather than assuming it lands the instant the `with websocket_connect`
# block exits.
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


def _wait_for_body_containing(path: str, needle: str, timeout: float = _CHECKPOINT_POLL_SECONDS) -> str:
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
    wiki_git.commit_file(_PATH + ".two", _SEED_BODY, message="seed", author="Ada <ada2@x.com>")
    path = _PATH + ".two"

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
