"""Provenance read side (app/wiki/provenance.py). Attribution resolution, the
batched history lookup, and the per-page source list. Real DB for the queries.
"""

from __future__ import annotations

from app.models.wiki import WriteProvenance
from app.wiki import provenance
from app.wiki.git import CommitInfo
from tests._seed import seed_user


def test_from_author_agent_via():
    a = provenance._from_author("Nik via launcher-claude-code")
    assert a.actor_kind == "agent"
    assert a.person == "Nik"
    assert a.agent == "Claude Code"


def test_from_author_ingestion_name():
    assert provenance._from_author("Onyx Ingest").actor_kind == "ingestion"


def test_from_author_plain_human():
    a = provenance._from_author("Jane Doe")
    assert a.actor_kind == "human"
    assert a.person == "Jane Doe"


def test_for_history_mixes_ledger_rows_and_author_fallback(tmp_db):
    uid = seed_user("usr_h", email="h@x.com", name="Nik")
    provenance.record(
        commit_sha="has-row",
        doc_path="P.md",
        user_id=uid,
        agent_name=None,
        agent_session_id=None,
        source=None,
    )
    rows = [
        CommitInfo(sha="has-row", author="ignored", ts="t", message="m", body=""),
        CommitInfo(sha="no-row", author="Nik via launcher-claude-code", ts="t", message="m", body=""),
    ]
    got = provenance.for_history(rows, "P.md")
    # the ledger row wins, and the row-less commit falls back to its author
    assert got["has-row"].person == "Nik"
    assert got["has-row"].actor_kind == "human"
    assert got["no-row"].actor_kind == "agent"
    assert got["no-row"].agent == "Claude Code"


def test_for_commits_joins_user_display(tmp_db):
    uid = seed_user("usr_r", email="r@x.com", name="Nik")
    provenance.record(
        commit_sha="c1",
        doc_path="P.md",
        user_id=uid,
        agent_name="onyx-craft",
        agent_session_id=None,
        source=None,
    )
    got = provenance.for_commits(["c1", "missing"], "P.md")
    assert set(got) == {"c1"}
    assert got["c1"].actor_kind == "agent"
    assert got["c1"].person == "Nik"
    # ledger agent slug is normalized the same way the author-parse fallback is
    assert got["c1"].agent == "Onyx Craft"


def test_for_commits_preserves_unmapped_agent_label(tmp_db):
    uid = seed_user("usr_ai", email="ai@x.com", name="AI")
    provenance.record(
        commit_sha="c2",
        doc_path="P.md",
        user_id=uid,
        agent_name="Wiki AI Assistant",
        agent_session_id=None,
        source=None,
    )
    assert provenance.for_commits(["c2"], "P.md")["c2"].agent == "Wiki AI Assistant"


def test_head_attribution_from_ledger(tmp_db):
    provenance.record(
        commit_sha="head1",
        doc_path="P.md",
        user_id=None,
        agent_name=None,
        agent_session_id=None,
        source=WriteProvenance(source_document_id="d1", source_title="Doc"),
    )
    a = provenance.head_attribution("P.md", "head1")
    assert a is not None
    assert a.actor_kind == "ingestion"
    assert a.source_title == "Doc"


def test_sources_for_path_dedups_newest_first(tmp_db):
    for sha, doc_id in [("s1", "dup"), ("s2", "dup"), ("s3", "other")]:
        provenance.record(
            commit_sha=sha,
            doc_path="P.md",
            user_id=None,
            agent_name=None,
            agent_session_id=None,
            source=WriteProvenance(source_document_id=doc_id, source_title=sha),
        )
    ids = [s.source_document_id for s in provenance.sources_for_path("P.md")]
    assert ids == ["other", "dup"]


def test_sources_for_path_keeps_anonymous_rows(tmp_db):
    for sha in ["a1", "a2"]:
        provenance.record(
            commit_sha=sha,
            doc_path="P.md",
            user_id=None,
            agent_name=None,
            agent_session_id=None,
            source=WriteProvenance(source_type="slack"),
        )
    assert len(provenance.sources_for_path("P.md")) == 2
