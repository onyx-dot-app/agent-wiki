"""Per-page CRDT document rows (app/wiki/wiki_documents.py) — the dual-write
mirror that shadows session snapshot state, plus the delete lifecycle hook.

DB-backed like ``test_coedit_repo.py``; exercised through the ``coedit``
entry points that carry the mirror writes, so the tests pin the lockstep
("a session snapshot write and its document mirror land together"), not just
the repo functions in isolation. Rows are keyed by ``wiki_doc_ids`` id —
moves are covered by re-keying the *registry* and observing the row follow,
since the table itself has no move hook to test.
"""
from __future__ import annotations

import pytest

from sqlalchemy import update

from app.db.models import CoeditSession, WikiDocument
from app.db.session import session as db_session
from app.models.wiki import PathMove
from app.wiki import coedit, doc_ids, wiki_documents
from tests._seed import count_rows, seed_user

_PATH = "guides/setup.md"


@pytest.fixture
def users(tmp_db):
    seed_user("usr_a", "a@x.com", name="Ada")
    return tmp_db


def _force_close(session_id: int) -> None:
    with db_session() as s:
        s.execute(
            update(CoeditSession)
            .where(CoeditSession.id == session_id)
            .values(status="closed")
        )


def test_initial_snapshot_mirrors_a_document_row(users):
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s.id, b"snap0", "hello")
    doc = wiki_documents.get(_PATH)
    assert doc is not None
    assert doc["ydoc_snapshot"] == b"snap0"
    assert doc["ydoc_snapshot_seq"] == 0
    assert doc["ydoc_snapshot_body"] == "hello"
    assert doc["ydoc_seq"] == 0
    assert doc["base_sha"] == "sha1"
    # The mirror minted the page's registry id and keyed the row by it.
    assert doc["doc_id"] == doc_ids.id_for_path(_PATH)


def test_mirror_adopts_an_existing_registry_id(users):
    minted = doc_ids.mint_for_page(_PATH)
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s.id, b"snap0", "hello")
    doc = wiki_documents.get(_PATH)
    assert doc is not None
    assert doc["doc_id"] == minted


def test_losing_seed_does_not_mirror(users):
    # The conditional seed's loser corresponds to no durable lineage — the
    # mirror must reflect only the snapshot that won.
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s.id, b"first", "one")
    coedit.set_initial_snapshot(s.id, b"second", "two")
    doc = wiki_documents.get(_PATH)
    assert doc is not None
    assert doc["ydoc_snapshot"] == b"first"
    assert doc["ydoc_snapshot_body"] == "one"


def test_reseed_on_a_new_session_overwrites_the_mirror(users):
    # While sessions own document state the mirror follows them: a fresh
    # session minting a new lineage for the page replaces the row. (Seed-once
    # is a cutover-phase property, not a dual-write one.)
    s1 = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s1.id, b"lineage1", "one")
    _force_close(s1.id)
    # A different base_sha defeats session reuse, so this is a fresh lineage.
    s2 = coedit.open_session(_PATH, base_sha="sha2")
    assert s2.id != s1.id
    coedit.set_initial_snapshot(s2.id, b"lineage2", "two")
    doc = wiki_documents.get(_PATH)
    assert doc is not None
    assert doc["ydoc_snapshot"] == b"lineage2"
    assert doc["base_sha"] == "sha2"
    assert count_rows(WikiDocument) == 1


def test_checkpoint_mirrors_advanced_state(users):
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s.id, b"snap0", "hello")
    coedit.apply_update(s.id, update_bytes=b"up1", author_user_id="usr_a")
    coedit.advance_checkpoint(
        s.id, seq=1, snapshot=b"snap1", body="hello world", base_sha="sha2"
    )
    doc = wiki_documents.get(_PATH)
    assert doc is not None
    assert doc["ydoc_snapshot"] == b"snap1"
    assert doc["ydoc_snapshot_seq"] == 1
    assert doc["ydoc_snapshot_body"] == "hello world"
    assert doc["ydoc_seq"] == 1
    assert doc["base_sha"] == "sha2"


def test_checkpoint_creates_the_row_for_a_pre_table_session(users):
    # A session opened before the migration ran has no document row; its
    # first checkpoint after the deploy mirrors one in.
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s.id, b"snap0", "hello")
    coedit.apply_update(s.id, update_bytes=b"up1", author_user_id="usr_a")
    wiki_documents.on_pages_deleted([_PATH])  # simulate the missing row
    coedit.advance_checkpoint(
        s.id, seq=1, snapshot=b"snap1", body="hello world", base_sha="sha2"
    )
    doc = wiki_documents.get(_PATH)
    assert doc is not None
    assert doc["ydoc_snapshot"] == b"snap1"


def test_regressed_checkpoint_does_not_mirror(users):
    # advance_checkpoint's regression guard must gate the mirror too.
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s.id, b"snap0", "hello")
    coedit.apply_update(s.id, update_bytes=b"up1", author_user_id="usr_a")
    coedit.apply_update(s.id, update_bytes=b"up2", author_user_id="usr_a")
    coedit.advance_checkpoint(s.id, seq=2, snapshot=b"snap2", body="two", base_sha="sha2")
    coedit.advance_checkpoint(s.id, seq=1, snapshot=b"snap1", body="one", base_sha="sha1")
    doc = wiki_documents.get(_PATH)
    assert doc is not None
    assert doc["ydoc_snapshot"] == b"snap2"
    assert doc["ydoc_snapshot_seq"] == 2


def test_set_base_sha_mirrors_the_merge_base(users):
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s.id, b"snap0", "hello")
    assert coedit.set_base_sha(s.id, "sha9")
    doc = wiki_documents.get(_PATH)
    assert doc is not None
    assert doc["base_sha"] == "sha9"


def test_set_base_sha_without_a_row_stays_absent(users):
    # A rebase can land before the snapshot seed; a document row can't exist
    # before its snapshot does, so the mirror is update-if-exists only — it
    # must not mint an id or create a row.
    s = coedit.open_session(_PATH, base_sha="sha1")
    assert coedit.set_base_sha(s.id, "sha9")
    assert wiki_documents.get(_PATH) is None
    assert count_rows(WikiDocument) == 0


def test_a_move_rekeys_the_registry_and_the_row_follows(users):
    # No move hook on the table: the row is keyed by id, so re-keying the
    # registry (what after_path_move does via doc_ids.on_path_moved) is the
    # whole move.
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s.id, b"snap0", "hello")
    before = wiki_documents.get(_PATH)
    assert before is not None
    doc_ids.on_path_moved([PathMove(old=_PATH, new="guides/install.md")])
    assert wiki_documents.get(_PATH) is None
    after = wiki_documents.get("guides/install.md")
    assert after is not None
    assert after["doc_id"] == before["doc_id"]
    assert after["ydoc_snapshot"] == b"snap0"


def test_on_pages_deleted_drops_the_row(users):
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s.id, b"snap0", "hello")
    wiki_documents.on_pages_deleted([_PATH])
    assert wiki_documents.get(_PATH) is None
    assert count_rows(WikiDocument) == 0


def test_recreate_after_delete_is_a_fresh_document(users):
    # Registry semantics carry over: a page recreated at a deleted path is a
    # new document (fresh id), and the mirror keys the new lineage under it.
    s1 = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s1.id, b"old", "old")
    old = wiki_documents.get(_PATH)
    assert old is not None
    wiki_documents.on_pages_deleted([_PATH])
    doc_ids.on_deleted(_PATH)
    _force_close(s1.id)
    s2 = coedit.open_session(_PATH, base_sha="sha2")
    coedit.set_initial_snapshot(s2.id, b"new", "new")
    fresh = wiki_documents.get(_PATH)
    assert fresh is not None
    assert fresh["doc_id"] != old["doc_id"]
    assert fresh["ydoc_snapshot"] == b"new"
    assert count_rows(WikiDocument) == 1


def test_on_pages_deleted_ignores_non_pages_and_missing_rows(users):
    wiki_documents.on_pages_deleted(["folder/notes.txt", "never-seen.md"])
    assert count_rows(WikiDocument) == 0
