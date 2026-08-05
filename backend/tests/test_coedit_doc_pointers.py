"""The session layer's document-identity binding (``coedit_sessions.doc_id``,
``coedit_updates.doc_id``) — stamped at open, carried onto every logged
update. Transition plumbing for the cutover that moves document state to
``wiki_documents``: nothing reads these pointers yet.
"""
from __future__ import annotations

import pytest

from sqlalchemy import select, update

from app.db.models import CoeditSession, CoeditUpdate
from app.db.session import session as db_session
from app.wiki import coedit, doc_ids
from tests._seed import seed_user

_PATH = "guides/setup.md"


@pytest.fixture
def users(tmp_db):
    seed_user("usr_a", "a@x.com", name="Ada")
    return tmp_db


def _update_doc_ids(session_id: int) -> list[str | None]:
    with db_session() as s:
        return list(
            s.scalars(
                select(CoeditUpdate.doc_id)
                .where(CoeditUpdate.session_id == session_id)
                .order_by(CoeditUpdate.seq)
            )
        )


def test_open_stamps_the_page_doc_id(users):
    minted = doc_ids.mint_for_page(_PATH)
    s = coedit.open_session(_PATH, base_sha="sha1")
    assert s.doc_id == minted


def test_open_without_a_registry_row_leaves_null(users):
    # Resolve-only, like the wiki_documents mirror: no live registry row, no
    # mint — the binding stays NULL until an open after the page is read.
    s = coedit.open_session(_PATH, base_sha="sha1")
    assert s.doc_id is None


def test_reopen_backfills_a_null_binding(users):
    s = coedit.open_session(_PATH, base_sha="sha1")
    assert s.doc_id is None
    minted = doc_ids.mint_for_page(_PATH)
    again = coedit.open_session(_PATH, base_sha="sha1")
    assert again.id == s.id
    assert again.doc_id == minted


def test_reactivation_backfills_a_null_binding(users):
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s.id, b"snap0", "hello")
    with db_session() as db:
        db.execute(
            update(CoeditSession).where(CoeditSession.id == s.id).values(status="closed")
        )
    minted = doc_ids.mint_for_page(_PATH)
    reused = coedit.open_session(_PATH, base_sha="sha1")
    assert reused.id == s.id  # reactivated, not fresh
    assert reused.doc_id == minted


def test_updates_carry_the_session_binding(users):
    minted = doc_ids.mint_for_page(_PATH)
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.apply_update(s.id, update_bytes=b"up1", author_user_id="usr_a")
    coedit.apply_update(s.id, update_bytes=b"up2", author_user_id="usr_a")
    assert _update_doc_ids(s.id) == [minted, minted]


def test_updates_under_a_null_binding_stay_null(users):
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.apply_update(s.id, update_bytes=b"up1", author_user_id="usr_a")
    assert _update_doc_ids(s.id) == [None]
