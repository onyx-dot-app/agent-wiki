"""Per-page CRDT document rows (app/wiki/wiki_documents.py) — the dual-write
mirror that shadows session snapshot state, plus the delete lifecycle hook.

DB-backed like ``test_coedit_repo.py``; exercised through the ``coedit``
entry points that carry the mirror writes, so the tests pin the lockstep
("a session snapshot write and its document mirror land together"), not just
the repo functions in isolation. Rows are keyed by ``wiki_doc_ids`` id —
moves are covered by re-keying the *registry* and observing the row follow,
since the table itself has no move hook to test. The fixture mints the
page's id up front, matching the production invariant the mirror relies on
(every page a session can exist for was read first, and reads mint).
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


@pytest.fixture
def page_id(users) -> str:
    return doc_ids.mint_for_page(_PATH)


def _force_close(session_id: int) -> None:
    with db_session() as s:
        s.execute(
            update(CoeditSession)
            .where(CoeditSession.id == session_id)
            .values(status="closed")
        )


def test_initial_snapshot_mirrors_a_document_row(page_id):
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s.id, b"snap0", "hello")
    doc = wiki_documents.get(_PATH)
    assert doc is not None
    assert doc["doc_id"] == page_id
    assert doc["ydoc_snapshot"] == b"snap0"
    assert doc["ydoc_snapshot_seq"] == 0
    assert doc["ydoc_snapshot_body"] == "hello"
    assert doc["ydoc_seq"] == 0
    assert doc["base_sha"] == "sha1"


def test_mirror_without_a_registry_id_skips(users):
    # The mirror resolves ids, never mints them: with no live registry row
    # (the mid-move window), the write is skipped — no phantom id, no stray
    # row — and the next checkpoint self-heals.
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s.id, b"snap0", "hello")
    assert doc_ids.id_for_path(_PATH) is None
    assert count_rows(WikiDocument) == 0
    doc_ids.mint_for_page(_PATH)
    coedit.apply_update(s.id, update_bytes=b"up1", author_user_id="usr_a")
    coedit.advance_checkpoint(s.id, seq=1, snapshot=b"snap1", body="healed", base_sha="sha2")
    doc = wiki_documents.get(_PATH)
    assert doc is not None
    assert doc["ydoc_snapshot_body"] == "healed"


def test_losing_seed_does_not_mirror(page_id):
    # The conditional seed's loser corresponds to no durable lineage — the
    # mirror must reflect only the snapshot that won.
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s.id, b"first", "one")
    coedit.set_initial_snapshot(s.id, b"second", "two")
    doc = wiki_documents.get(_PATH)
    assert doc is not None
    assert doc["ydoc_snapshot"] == b"first"
    assert doc["ydoc_snapshot_body"] == "one"


def test_reseed_on_a_new_session_overwrites_the_mirror(page_id):
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


def test_checkpoint_mirrors_advanced_state(page_id):
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


def test_checkpoint_creates_the_row_for_a_pre_table_session(page_id):
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


def test_regressed_checkpoint_does_not_mirror(page_id):
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


def test_set_base_sha_mirrors_the_merge_base(page_id):
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s.id, b"snap0", "hello")
    assert coedit.set_base_sha(s.id, "sha9")
    doc = wiki_documents.get(_PATH)
    assert doc is not None
    assert doc["base_sha"] == "sha9"


def test_set_base_sha_without_a_row_stays_absent(page_id):
    # A rebase can land before the snapshot seed; a document row can't exist
    # before its snapshot does, so the mirror is update-if-exists only.
    s = coedit.open_session(_PATH, base_sha="sha1")
    assert coedit.set_base_sha(s.id, "sha9")
    assert wiki_documents.get(_PATH) is None
    assert count_rows(WikiDocument) == 0


def test_a_move_rekeys_the_registry_and_the_row_follows(page_id):
    # No move hook on the table: the row is keyed by id, so re-keying the
    # registry (what after_path_move does via doc_ids.on_path_moved) is the
    # whole move.
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s.id, b"snap0", "hello")
    doc_ids.on_path_moved([PathMove(old=_PATH, new="guides/install.md")])
    assert wiki_documents.get(_PATH) is None
    after = wiki_documents.get("guides/install.md")
    assert after is not None
    assert after["doc_id"] == page_id
    assert after["ydoc_snapshot"] == b"snap0"


def test_move_window_checkpoint_is_repaired_by_the_session_rekey(page_id):
    # The mid-move race, replayed in hook order: the registry re-keys first;
    # a dirty checkpoint lands before the session re-key, resolves the old
    # path, and skips its mirror (leaving the session clean — nothing further
    # would write); then coedit.on_path_moved re-keys the session and
    # re-mirrors its snapshot state, repairing the row without another edit.
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s.id, b"snap0", "hello")
    coedit.apply_update(s.id, update_bytes=b"up1", author_user_id="usr_a")
    new_path = "guides/install.md"
    doc_ids.on_path_moved([PathMove(old=_PATH, new=new_path)])
    coedit.advance_checkpoint(s.id, seq=1, snapshot=b"snap1", body="moved", base_sha="sha2")
    stale = wiki_documents.get(new_path)
    assert stale is not None
    assert stale["ydoc_snapshot"] == b"snap0"  # the checkpoint's mirror skipped
    coedit.on_path_moved([PathMove(old=_PATH, new=new_path)])
    repaired = wiki_documents.get(new_path)
    assert repaired is not None
    assert repaired["doc_id"] == page_id
    assert repaired["ydoc_snapshot"] == b"snap1"
    assert repaired["ydoc_snapshot_seq"] == 1
    assert repaired["base_sha"] == "sha2"
    assert count_rows(WikiDocument) == 1


def test_trash_rekey_does_not_recreate_the_row(page_id):
    # after_doc_trashed order: sessions re-key into .trash first, then the
    # row drops, then ids tombstone. The re-key's resync must not resurrect
    # a row for the trashed page — .trash paths resolve no live id, so the
    # mirror skips itself.
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s.id, b"snap0", "hello")
    trash_path = ".trash/abc123/setup.md"
    coedit.on_path_moved([PathMove(old=_PATH, new=trash_path)])
    wiki_documents.on_pages_deleted([_PATH])
    doc_ids.on_deleted(_PATH)
    assert count_rows(WikiDocument) == 0


def test_on_pages_deleted_drops_the_row(page_id):
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s.id, b"snap0", "hello")
    wiki_documents.on_pages_deleted([_PATH])
    assert wiki_documents.get(_PATH) is None
    assert count_rows(WikiDocument) == 0


def test_recreate_after_delete_is_a_fresh_document(page_id):
    # Registry semantics carry over: a page recreated at a deleted path is a
    # new document (fresh id), and the mirror keys the new lineage under it.
    s1 = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s1.id, b"old", "old")
    wiki_documents.on_pages_deleted([_PATH])
    doc_ids.on_deleted(_PATH)
    _force_close(s1.id)
    # The recreate's read mints the fresh id before any session exists.
    fresh_id = doc_ids.get_or_mint(_PATH)
    assert fresh_id != page_id
    s2 = coedit.open_session(_PATH, base_sha="sha2")
    coedit.set_initial_snapshot(s2.id, b"new", "new")
    fresh = wiki_documents.get(_PATH)
    assert fresh is not None
    assert fresh["doc_id"] == fresh_id
    assert fresh["ydoc_snapshot"] == b"new"
    assert count_rows(WikiDocument) == 1


def test_on_pages_deleted_ignores_non_pages_and_missing_rows(users):
    wiki_documents.on_pages_deleted(["folder/notes.txt", "never-seen.md"])
    assert count_rows(WikiDocument) == 0


def test_advance_offline_cas_yields_to_newer_state(page_id):
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s.id, b"snap0", "hello")
    # Expected base doesn't match the row's — a checkpoint or re-mirror won;
    # the offline fold must report False and leave the row alone.
    assert not wiki_documents.advance_offline(
        _PATH, snapshot=b"snap1", body="new", base_sha="sha2", expected_base_sha="wrong"
    )
    row = wiki_documents.get(_PATH)
    assert row is not None and row["ydoc_snapshot"] == b"snap0"
    assert wiki_documents.advance_offline(
        _PATH, snapshot=b"snap1", body="new", base_sha="sha2", expected_base_sha="sha1"
    )
    row = wiki_documents.get(_PATH)
    assert row is not None
    assert row["ydoc_snapshot"] == b"snap1"
    assert row["base_sha"] == "sha2"
