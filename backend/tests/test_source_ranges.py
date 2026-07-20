"""Source ranges (app/wiki/provenance.py service + app/db/provenance.py repo +
provenance_remap.py). Diff-to-span capture, the ledger-id return that links
them, the remap CRUD and orchestration, move follow, the per-page source list,
and the content-span read. Real DB, and a tmp git repo for the remap tests.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import ProvenanceLedger, SourceRange
from app.db.session import session
from app.models.wiki import WriteProvenance
from app.wiki import git as wiki_git
from app.db import provenance as db_provenance
from app.wiki import notify, provenance, provenance_remap

_PATH = "notes.md"


def _ingest(
    sha: str,
    *,
    body: str = "body text",
    doc_path: str = _PATH,
    source: WriteProvenance | None = None,
) -> int:
    """Record an ingestion ledger row and capture its spans from an empty base.
    Returns the ledger id."""
    pid = provenance.record(
        commit_sha=sha,
        doc_path=doc_path,
        user_id=None,
        agent_name=None,
        agent_session_id=None,
        source=source or WriteProvenance(source_document_id="d1"),
    )
    assert pid is not None
    provenance.capture_source_ranges(
        provenance_id=pid, doc_path=doc_path, anchor_sha=sha, old_body="", new_body=body
    )
    return pid


def _seed_range(anchor_sha: str = "c1", doc_path: str = "P.md") -> int:
    """Ingest one span and return its range id."""
    pid = _ingest(anchor_sha, doc_path=doc_path)
    with session() as s:
        rid = s.scalar(select(SourceRange.id).where(SourceRange.provenance_id == pid))
    assert rid is not None
    return rid


def test_changed_spans_reports_new_body_replace_and_insert():
    assert provenance.changed_spans("hello world", "hello brave world") == [(6, 12)]
    assert provenance.changed_spans("abc", "aXc") == [(1, 2)]
    assert provenance.changed_spans("ab", "abcd") == [(2, 4)]


def test_changed_spans_ignores_pure_deletion_and_no_change():
    assert provenance.changed_spans("abc", "ac") == []
    assert provenance.changed_spans("same", "same") == []


def test_record_returns_ledger_id(tmp_db):
    pid = provenance.record(
        commit_sha="c1",
        doc_path="P.md",
        user_id=None,
        agent_name=None,
        agent_session_id=None,
        source=WriteProvenance(source_document_id="d1"),
    )
    assert isinstance(pid, int)
    with session() as s:
        assert s.get(ProvenanceLedger, pid) is not None


def test_record_returns_existing_id_on_conflict(tmp_db):
    def _rec() -> int | None:
        return provenance.record(
            commit_sha="dup",
            doc_path="P.md",
            user_id=None,
            agent_name=None,
            agent_session_id=None,
            source=None,
        )

    first = _rec()
    assert first is not None
    assert _rec() == first


def test_capture_source_ranges_links_to_ledger(tmp_db):
    pid = provenance.record(
        commit_sha="c1",
        doc_path="P.md",
        user_id=None,
        agent_name=None,
        agent_session_id=None,
        source=WriteProvenance(source_document_id="d1"),
    )
    assert pid is not None
    provenance.capture_source_ranges(
        provenance_id=pid,
        doc_path="P.md",
        anchor_sha="c1",
        old_body="hello world",
        new_body="hello brave world",
    )
    with session() as s:
        rows = s.scalars(select(SourceRange).where(SourceRange.provenance_id == pid)).all()
    assert len(rows) == 1
    r = rows[0]
    assert (r.start_offset, r.end_offset) == (6, 12)
    assert r.quoted_text == "brave "
    assert r.status == "live"
    assert r.anchor_sha == "c1"


def test_capture_source_ranges_no_op_when_unchanged(tmp_db):
    pid = provenance.record(
        commit_sha="c1",
        doc_path="P.md",
        user_id=None,
        agent_name=None,
        agent_session_id=None,
        source=WriteProvenance(source_document_id="d1"),
    )
    assert pid is not None
    provenance.capture_source_ranges(
        provenance_id=pid, doc_path="P.md", anchor_sha="c1", old_body="same", new_body="same"
    )
    with session() as s:
        assert s.scalars(select(SourceRange)).all() == []


def test_live_ranges_needing_remap_selects_stale_live_only(tmp_db):
    rid = _seed_range("old-sha")
    assert [r["id"] for r in db_provenance.live_ranges_needing_remap("P.md", "new-head")] == [rid]
    # a range already at HEAD is not stale
    assert db_provenance.live_ranges_needing_remap("P.md", "old-sha") == []


def test_apply_range_remap_advances_anchor(tmp_db):
    rid = _seed_range("old-sha")
    db_provenance.apply_range_remap(
        rid, start_offset=2, end_offset=5, quoted_text="dy ", anchor_sha="new-head"
    )
    with session() as s:
        r = s.get(SourceRange, rid)
    assert r is not None
    assert (r.start_offset, r.end_offset, r.anchor_sha, r.status) == (2, 5, "new-head", "live")


def test_retire_range_marks_retired_and_drops_from_remap(tmp_db):
    rid = _seed_range("old-sha")
    db_provenance.retire_range(rid)
    with session() as s:
        r = s.get(SourceRange, rid)
    assert r is not None and r.status == "retired"
    assert db_provenance.live_ranges_needing_remap("P.md", "new-head") == []


def test_rename_doc_repoints_ledger_and_ranges(tmp_db):
    _seed_range("c1")
    db_provenance.rename_doc("P.md", "Moved.md")
    with session() as s:
        assert (
            s.scalars(select(ProvenanceLedger).where(ProvenanceLedger.doc_path == "P.md")).all()
            == []
        )
        assert s.scalars(select(SourceRange).where(SourceRange.doc_path == "P.md")).all() == []
        assert (
            len(s.scalars(select(SourceRange).where(SourceRange.doc_path == "Moved.md")).all()) == 1
        )


def test_remap_source_ranges_advances_surviving_span(tmp_repo):
    body1 = "The target sentence stays put.\n"
    sha1 = wiki_git.commit_file(_PATH, body1, "create")
    pid = _ingest(sha1, body=body1)
    sha2 = wiki_git.commit_file(_PATH, "Intro added.\n" + body1, "edit")
    provenance_remap.remap_source_ranges(_PATH)
    with session() as s:
        r = s.scalars(select(SourceRange).where(SourceRange.provenance_id == pid)).one()
    assert r.status == "live"
    assert r.anchor_sha == sha2


def test_remap_source_ranges_retires_rewritten_span(tmp_repo):
    body1 = "This whole line gets replaced.\n"
    sha1 = wiki_git.commit_file(_PATH, body1, "create")
    pid = _ingest(sha1, body=body1)
    wiki_git.commit_file(_PATH, "Completely different content here now.\n", "edit")
    provenance_remap.remap_source_ranges(_PATH)
    with session() as s:
        r = s.scalars(select(SourceRange).where(SourceRange.provenance_id == pid)).one()
    assert r.status == "retired"


def test_after_path_move_follows_and_remaps_source_ranges(tmp_repo):
    body = "The ingested sentence.\n"
    sha1 = wiki_git.commit_file("a.md", body, "seed", author=None)
    pid = _ingest(sha1, body=body, doc_path="a.md")
    move_sha, moves = wiki_git.move_path("a.md", "b.md", "move", author=None)
    notify.after_path_move(moves, move_sha, actor=None)
    with session() as s:
        r = s.scalars(select(SourceRange).where(SourceRange.provenance_id == pid)).one()
    # followed the move, and re-anchored to the move commit (remap ran after re-key)
    assert r.doc_path == "b.md"
    assert r.anchor_sha == move_sha
    assert r.status == "live"


def test_sources_for_path_hides_source_once_all_spans_retired(tmp_db):
    _ingest("s1", body="alpha text", source=WriteProvenance(source_document_id="d1"))
    pid2 = _ingest("s2", body="beta text", source=WriteProvenance(source_document_id="d2"))
    with session() as s:
        rid = s.scalar(select(SourceRange.id).where(SourceRange.provenance_id == pid2))
    assert rid is not None
    db_provenance.retire_range(rid)
    # d1 has a live span. d2's only span is retired, so d2 drops off
    ids = [r.source_document_id for r in provenance.sources_for_path(_PATH)]
    assert ids == ["d1"]


def test_sources_for_path_shows_ingest_with_no_captured_range(tmp_db):
    # a row that never captured a span (no-content ingest, or pre-capture) has no
    # evidence its content is gone, so it stays listed
    provenance.record(
        commit_sha="s1",
        doc_path=_PATH,
        user_id=None,
        agent_name=None,
        agent_session_id=None,
        source=WriteProvenance(source_document_id="d1"),
    )
    ids = [r.source_document_id for r in provenance.sources_for_path(_PATH)]
    assert ids == ["d1"]


def test_sources_for_path_dedups_newest_live_first(tmp_db):
    _ingest("s1", body="a", source=WriteProvenance(source_document_id="dup", source_title="s1"))
    _ingest("s2", body="b", source=WriteProvenance(source_document_id="dup", source_title="s2"))
    _ingest("s3", body="c", source=WriteProvenance(source_document_id="other", source_title="s3"))
    ids = [r.source_document_id for r in provenance.sources_for_path(_PATH)]
    assert ids == ["other", "dup"]


def test_sources_for_path_keeps_anonymous_rows(tmp_db):
    _ingest("a1", body="x", source=WriteProvenance(source_type="slack"))
    _ingest("a2", body="y", source=WriteProvenance(source_type="slack"))
    assert len(provenance.sources_for_path(_PATH)) == 2


def test_live_spans_for_doc_orders_and_carries_source(tmp_db):
    pid = provenance.record(
        commit_sha="c1",
        doc_path=_PATH,
        user_id=None,
        agent_name=None,
        agent_session_id=None,
        source=WriteProvenance(source_document_id="d1", source_url="http://x"),
    )
    assert pid is not None
    provenance.capture_source_ranges(
        provenance_id=pid, doc_path=_PATH, anchor_sha="c1", old_body="ac", new_body="aXcY"
    )
    spans = db_provenance.live_spans_for_doc(_PATH, "c1")
    assert [(s["start_offset"], s["end_offset"]) for s in spans] == [(1, 2), (3, 4)]
    assert all(s["source_document_id"] == "d1" and s["source_url"] == "http://x" for s in spans)


def test_live_spans_for_doc_excludes_retired(tmp_db):
    pid = _ingest("c1", body="hello", source=WriteProvenance(source_document_id="d1"))
    with session() as s:
        rid = s.scalar(select(SourceRange.id).where(SourceRange.provenance_id == pid))
    assert rid is not None
    db_provenance.retire_range(rid)
    assert db_provenance.live_spans_for_doc(_PATH, "c1") == []


def test_content_spans_for_path_remaps_to_head(tmp_repo):
    body1 = "The target sentence stays put.\n"
    sha1 = wiki_git.commit_file(_PATH, body1, "create")
    _ingest(sha1, body=body1)
    prefix = "Intro added.\n"
    wiki_git.commit_file(_PATH, prefix + body1, "edit")
    spans = provenance.content_spans_for_path(_PATH)
    assert len(spans) == 1
    # the span followed the edit: its offset shifted by the prepended prefix
    assert spans[0].start_offset == len(prefix)


def test_content_spans_omit_span_stuck_on_unreachable_anchor(tmp_repo):
    body = "Some page body here.\n"
    wiki_git.commit_file(_PATH, body, "create")
    # a span anchored to a sha the repo cannot read: remap skips it, and the
    # head-anchored read then omits it instead of returning stale offsets
    _ingest("deadbeef" * 5, body=body)
    assert provenance.content_spans_for_path(_PATH) == []
