"""The attach path (``_seed_snapshot_sync`` + ``coedit.transplant_from_document``)
— a new session adopts the page's persistent document from ``wiki_documents``
instead of seeding a fresh lineage from markdown.

This is the cutover that makes a second lineage per page structurally
impossible: the incident shape (a client retains its document, the session
row is purged, the reconnect used to get a freshly seeded lineage and the
retained document re-merged as a full duplicate) now converges, because the
transplanted snapshot *is* the lineage the client holds.
"""
from __future__ import annotations

import pytest

from pycrdt import Doc, XmlFragment

from app.api.coedit import _seed_snapshot_sync
from app.db.models import CoeditSession
from app.db.session import session as db_session
from app.wiki import coedit, doc_ids, wiki_documents
from app.wiki.markdown_yjs import ROOT_XML_KEY, reconstruct_body, seed_doc_from_markdown
from tests._seed import seed_user

_PATH = "guides/setup.md"
_BODY = "# Setup\n\nInstall the thing.\n"


@pytest.fixture
def users(tmp_db):
    seed_user("usr_a", "a@x.com", name="Ada")
    return tmp_db


def _snapshot(session_id: int) -> bytes:
    row = coedit.get_session_for_checkpoint(session_id)
    assert row is not None and row.ydoc_snapshot is not None
    return row.ydoc_snapshot


def _purge(session_id: int) -> None:
    with db_session() as s:
        row = s.get(CoeditSession, session_id)
        assert row is not None
        s.delete(row)


def _seed_first_session(monkeypatch) -> tuple[int, bytes]:
    """A page's first-ever session, seeded from markdown (no document row
    exists yet); the slice-1 mirror creates the row from this seed."""
    doc_ids.mint_for_page(_PATH)
    monkeypatch.setattr("app.api.coedit.git.read_file_opt", lambda *_a, **_k: _BODY)
    monkeypatch.setattr("app.api.coedit.git.head_sha_for_path", lambda *_a, **_k: "sha1")
    s1 = coedit.open_session(_PATH, base_sha="sha1")
    assert _seed_snapshot_sync(s1.id, _PATH, "sha1")
    return s1.id, _snapshot(s1.id)


def test_first_seed_creates_the_document_row(users, monkeypatch):
    _seed_first_session(monkeypatch)
    doc = wiki_documents.get(_PATH)
    assert doc is not None
    assert doc["ydoc_snapshot_body"] == _BODY


def test_reopen_after_purge_transplants_the_lineage(users, monkeypatch):
    s1_id, original = _seed_first_session(monkeypatch)
    _purge(s1_id)  # the incident shape: the row reuse would need is gone
    s2 = coedit.open_session(_PATH, base_sha="sha1")
    assert s2.id != s1_id
    folds: list[tuple[int, str]] = []
    monkeypatch.setattr(
        "app.api.coedit.rebase_coedit_session", lambda sid, sha: folds.append((sid, sha))
    )
    assert _seed_snapshot_sync(s2.id, _PATH, "sha1")
    assert _snapshot(s2.id) == original  # the same lineage, byte for byte
    assert folds == []  # base_sha matches HEAD — nothing to fold


def test_retained_client_document_does_not_duplicate(users, monkeypatch):
    # The end-to-end bug kill: a client's retained document, synced into the
    # session that replaced its purged one, must merge as a no-op — not as a
    # second copy of every block.
    s1_id, original = _seed_first_session(monkeypatch)
    retained = Doc()
    retained.apply_update(original)  # the browser's copy of the document
    _purge(s1_id)
    s2 = coedit.open_session(_PATH, base_sha="sha1")
    monkeypatch.setattr("app.api.coedit.rebase_coedit_session", lambda *_a: None)
    assert _seed_snapshot_sync(s2.id, _PATH, "sha1")
    server = Doc()
    server.apply_update(_snapshot(s2.id))
    blocks_before = len(server.get(ROOT_XML_KEY, type=XmlFragment).children)
    server.apply_update(retained.get_update())  # SYNC_STEP1's full-state reply
    blocks_after = len(server.get(ROOT_XML_KEY, type=XmlFragment).children)
    assert blocks_after == blocks_before
    assert reconstruct_body(server) == reconstruct_body(retained)


def test_pre_cutover_reseed_shape_duplicates(users):
    # The control: two independent seeds of the same markdown DO duplicate on
    # merge — the behavior the attach path exists to remove. If this ever
    # starts passing with equal counts, the codec changed and the attach
    # rationale should be revisited.
    a = seed_doc_from_markdown(_BODY)
    b = seed_doc_from_markdown(_BODY)
    blocks_single = len(a.get(ROOT_XML_KEY, type=XmlFragment).children)
    a.apply_update(b.get_update())
    blocks_merged = len(a.get(ROOT_XML_KEY, type=XmlFragment).children)
    assert blocks_merged == 2 * blocks_single


def test_transplant_folds_head_drift(users, monkeypatch):
    s1_id, original = _seed_first_session(monkeypatch)
    _purge(s1_id)
    # An out-of-band commit lands while nobody has the page open: the
    # document row's base_sha stays at sha1 while HEAD moves to sha2.
    monkeypatch.setattr("app.api.coedit.git.head_sha_for_path", lambda *_a, **_k: "sha2")
    s2 = coedit.open_session(_PATH, base_sha="sha2")
    folds: list[tuple[int, str]] = []
    monkeypatch.setattr(
        "app.api.coedit.rebase_coedit_session", lambda sid, sha: folds.append((sid, sha))
    )
    assert _seed_snapshot_sync(s2.id, _PATH, "sha2")
    assert _snapshot(s2.id) == original  # lineage kept even across the drift
    assert folds == [(s2.id, "sha2")]  # ...and the drift queued as a fold
    row = coedit.get_session_for_checkpoint(s2.id)
    assert row is not None
    assert row.base_sha == "sha1"  # the snapshot's truth, until the fold lands


def test_transplant_race_is_harmless(users, monkeypatch):
    _seed_first_session(monkeypatch)
    s1 = coedit.get_active_session(_PATH)
    assert s1 is not None
    _purge(s1.id)
    s2 = coedit.open_session(_PATH, base_sha="sha1")
    monkeypatch.setattr("app.api.coedit.rebase_coedit_session", lambda *_a: None)
    # Two connections seed concurrently; both transplant the same lineage,
    # so even the "loser" lost nothing.
    assert _seed_snapshot_sync(s2.id, _PATH, "sha1")
    first = _snapshot(s2.id)
    assert _seed_snapshot_sync(s2.id, _PATH, "sha1")
    assert _snapshot(s2.id) == first
