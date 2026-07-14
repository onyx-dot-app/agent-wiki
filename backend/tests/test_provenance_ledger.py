"""Provenance ledger (app/wiki/provenance.py service + app/db/provenance.py
repo). Actor classification, the append-only insert, the move re-key, and the
delete cleanup.
Real DB. The table lands via the migration chain.
"""

from __future__ import annotations

from sqlalchemy import select

from app.auth.users import UserKind
from app.db.models import ProvenanceLedger, User
from app.db.session import session
from app.db import provenance as db_provenance
from app.models.wiki import WriteProvenance
from app.wiki import git as wiki_git
from app.wiki import notify, provenance
from tests._seed import seed_user


def test_actor_kind_ingestion_wins_over_user():
    src = WriteProvenance(source_document_id="doc-1", source_type="slack")
    assert provenance._actor_kind("human", "Claude Code", src) == "ingestion"


def test_actor_kind_agent_when_agent_name_bound():
    assert provenance._actor_kind("human", "Claude Code", None) == "agent"


def test_actor_kind_human_plain_edit():
    assert provenance._actor_kind("human", None, None) == "human"


def test_actor_kind_system_for_system_principal_and_userless():
    assert provenance._actor_kind(UserKind.SYSTEM.value, "x", None) == "system"
    assert provenance._actor_kind(None, None, None) == "system"


def test_actor_kind_ingestion_without_document_id():
    # A connector may omit the id, but a WriteProvenance is still an ingest write.
    src = WriteProvenance(source_type="slack", source_url="https://s/x")
    assert provenance._actor_kind(None, None, src) == "ingestion"


def test_record_persists_ingestion_row(tmp_db):
    provenance.record(
        commit_sha="abc123",
        doc_path="Foo/Bar.md",
        user_id=None,
        agent_name=None,
        agent_session_id=None,
        source=WriteProvenance(
            source_document_id="doc-9",
            source_type="gdrive",
            source_url="https://example.com/q3",
            source_title="Q3 Planning",
        ),
    )
    with session() as s:
        row = s.scalar(select(ProvenanceLedger).where(ProvenanceLedger.commit_sha == "abc123"))
    assert row is not None
    assert row.actor_kind == "ingestion"
    assert row.doc_path == "Foo/Bar.md"
    assert row.source_document_id == "doc-9"
    assert row.source_url == "https://example.com/q3"


def test_record_persists_human_row(tmp_db):
    uid = seed_user("usr_prov", email="prov@x.com")
    provenance.record(
        commit_sha="human1",
        doc_path="P.md",
        user_id=uid,
        agent_name=None,
        agent_session_id=None,
        source=None,
    )
    with session() as s:
        row = s.scalar(select(ProvenanceLedger).where(ProvenanceLedger.commit_sha == "human1"))
    assert row is not None
    assert row.actor_kind == "human"
    assert row.user_id == uid
    assert row.source_document_id is None


def test_record_idempotent_on_commit_and_path(tmp_db):
    for _ in range(2):
        provenance.record(
            commit_sha="dup1",
            doc_path="P.md",
            user_id=None,
            agent_name=None,
            agent_session_id=None,
            source=None,
        )
    with session() as s:
        rows = s.scalars(
            select(ProvenanceLedger).where(ProvenanceLedger.commit_sha == "dup1")
        ).all()
    assert len(rows) == 1
    assert rows[0].actor_kind == "system"


def test_record_classifies_a_system_principal(tmp_db):
    uid = seed_user("usr_sys", email="sys@x.com")
    with session() as s:
        u = s.get(User, uid)
        assert u is not None
        u.kind = UserKind.SYSTEM.value
    provenance.record(
        commit_sha="s1",
        doc_path="P.md",
        user_id=uid,
        agent_name="x",
        agent_session_id=None,
        source=None,
    )
    with session() as s:
        row = s.scalar(select(ProvenanceLedger).where(ProvenanceLedger.commit_sha == "s1"))
    assert row is not None
    assert row.actor_kind == "system"


def test_rename_doc_follows_a_page_move(tmp_db):
    provenance.record(
        commit_sha="m1",
        doc_path="Old.md",
        user_id=None,
        agent_name=None,
        agent_session_id=None,
        source=None,
    )
    db_provenance.rename_doc("Old.md", "New.md")
    with session() as s:
        row = s.scalar(select(ProvenanceLedger).where(ProvenanceLedger.commit_sha == "m1"))
    assert row is not None
    assert row.doc_path == "New.md"


def test_delete_for_doc_removes_ledger_rows(tmp_db):
    provenance.record(
        commit_sha="m1",
        doc_path="Gone.md",
        user_id=None,
        agent_name=None,
        agent_session_id=None,
        source=None,
    )
    db_provenance.delete_for_doc("Gone.md")
    with session() as s:
        assert (
            s.scalars(select(ProvenanceLedger).where(ProvenanceLedger.doc_path == "Gone.md")).all()
            == []
        )


def test_after_doc_delete_drops_provenance(tmp_repo):
    sha = wiki_git.commit_file("Doomed.md", "# Doomed\n", "seed", author=None)
    provenance.record(
        commit_sha=sha,
        doc_path="Doomed.md",
        user_id=None,
        agent_name=None,
        agent_session_id=None,
        source=None,
    )
    notify.after_doc_delete("Doomed.md", sha, actor=None)
    with session() as s:
        assert (
            s.scalars(
                select(ProvenanceLedger).where(ProvenanceLedger.doc_path == "Doomed.md")
            ).all()
            == []
        )
