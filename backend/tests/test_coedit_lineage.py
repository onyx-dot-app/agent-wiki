"""Lineage-generation guard (``coedit_sessions.ydoc_lineage``) — the reseed
poison fix.

When the checkpoint engine reseeds a session's document (the diverged branch's
non-splice-safe fallback), the new snapshot is a fresh CRDT lineage. A Yjs
update produced against the replaced lineage can never converge with it:
merging the two unions both documents' content — whole-page duplication —
rather than conflicting. The guard has three parts, each pinned here:

- ``apply_update(expected_lineage=...)`` refuses a stale-generation update
  with ``StaleLineageError`` instead of logging it.
- the reseed bumps ``ydoc_lineage`` atomically with the snapshot swap and
  broadcasts ``resync_required`` so connected clients rebuild.
- a rebuild replays only current-generation rows, so a leftover stale row
  (logged in the pre-bump window) is skipped, not unioned in.
"""

from __future__ import annotations

import pytest
from pycrdt import Doc

from app.auth import users as users_repo
from app.wiki import coedit, coedit_channel, coedit_checkpoint, doc_ids, markdown_splice, markdown_yjs
from app.wiki import git as wiki_git

_PATH = "guides/lineage.md"
_BODY = "alpha one\n\nbeta two\n"


def _seed_page(body: str) -> str:
    sha = wiki_git.commit_file(_PATH, body, "seed", author="Seed <seed@x.com>")
    # Raw git seeding bypasses the lifecycle hooks, so mint the registry id
    # they would have — the document-row mirror resolves paths through it.
    doc_ids.mint_for_page(_PATH)
    return sha


def _open_with_client(body: str) -> tuple[coedit.SessionRow, Doc, str]:
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page(body)
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)
    doc = markdown_yjs.seed_doc_from_markdown(body)
    coedit.set_initial_snapshot(sess.id, doc.get_update(), body)
    return sess, doc, uid


def _delta_edit(doc: Doc, prefix: str) -> bytes:
    from app.wiki.markdown_yjs import ROOT_XML_KEY
    from pycrdt import XmlFragment

    before = doc.get_state()
    root = doc.get(ROOT_XML_KEY, type=XmlFragment)
    with doc.transaction():
        root.children[0].children[0].insert(0, prefix)
    return doc.get_update(before)


def test_apply_update_refuses_stale_lineage(tmp_repo):
    sess, doc, uid = _open_with_client(_BODY)
    update = _delta_edit(doc, "X ")

    # Current generation: accepted.
    seq = coedit.apply_update(
        sess.id, update_bytes=update, author_user_id=uid, expected_lineage=0
    )
    assert seq is not None

    # Bump the generation the way a reseed does (same snapshot content is
    # fine — the guard is on the counter, not the bytes).
    coedit.advance_checkpoint(
        sess.id,
        seq=seq,
        snapshot=doc.get_update(),
        body=_BODY,
        base_sha=sess.base_sha or "",
        bump_lineage=True,
    )
    refreshed = coedit.get_session(sess.id)
    assert refreshed is not None and refreshed.ydoc_lineage == 1

    # A stale-generation update is refused, not logged.
    stale = _delta_edit(doc, "Y ")
    with pytest.raises(coedit.StaleLineageError):
        coedit.apply_update(
            sess.id, update_bytes=stale, author_user_id=uid, expected_lineage=0
        )
    assert coedit.updates_since(sess.id, 0).updates == []  # pruned + nothing new

    # The current generation still flows, and rows are stamped with it.
    seq2 = coedit.apply_update(
        sess.id, update_bytes=stale, author_user_id=uid, expected_lineage=1
    )
    assert seq2 is not None
    rows = coedit.updates_since(sess.id, seq).updates
    assert [r.lineage for r in rows] == [1]


def test_reseed_bumps_lineage_and_broadcasts_resync(tmp_repo, monkeypatch):
    sess, doc, uid = _open_with_client(_BODY)
    # Local edit on paragraph 1...
    coedit.apply_update(
        sess.id, update_bytes=_delta_edit(doc, "EDIT "), author_user_id=uid
    )
    # ...and an out-of-band commit on paragraph 2, so the checkpoint's merge
    # diverges from what the doc holds.
    wiki_git.commit_file(_PATH, "alpha one\n\nbeta CHANGED\n", "oob", author="X <x@x.com>")

    # Force the non-splice-safe fallback: the divergence must reseed.
    monkeypatch.setattr(markdown_splice, "apply_markdown_diff", lambda *a, **k: False)
    frames: list[tuple[int, dict[str, object]]] = []
    monkeypatch.setattr(
        coedit_channel, "publish_control", lambda sid, frame: frames.append((sid, frame))
    )

    outcome = coedit_checkpoint.checkpoint_session(sess.id)

    assert outcome is not None and outcome.diverged
    refreshed = coedit.get_session(sess.id)
    assert refreshed is not None and refreshed.ydoc_lineage == 1
    assert (sess.id, {"type": "resync_required", "reason": "reseeded"}) in frames
    # The merge result itself is intact — both sides present exactly once.
    committed = wiki_git.read_file(_PATH)
    assert committed.count("EDIT alpha one") == 1
    assert committed.count("beta CHANGED") == 1


def test_new_session_inherits_document_lineage(tmp_repo, monkeypatch):
    """The generation survives session turnover: a brand-new session serving a
    reseeded document starts at the document's generation, not 0 — otherwise a
    client that slept through the reseed would find its stale generation
    "matching" the fresh session row and slip past the guard."""
    sess, doc, uid = _open_with_client(_BODY)
    coedit.apply_update(
        sess.id, update_bytes=_delta_edit(doc, "EDIT "), author_user_id=uid
    )
    wiki_git.commit_file(_PATH, "alpha one\n\nbeta CHANGED\n", "oob", author="X <x@x.com>")
    monkeypatch.setattr(markdown_splice, "apply_markdown_diff", lambda *a, **k: False)
    assert coedit_checkpoint.checkpoint_session(sess.id) is not None
    coedit.close_session(sess.id)

    sess2 = coedit.open_session(_PATH, base_sha=wiki_git.head_sha_for_path(_PATH))
    assert sess2.id != sess.id
    attached, _base = coedit.transplant_from_document(sess2.id, _PATH)
    assert attached
    fresh = coedit.get_session(sess2.id)
    assert fresh is not None and fresh.ydoc_lineage == 1


def test_rebuild_skips_replaced_lineage_rows(tmp_repo):
    sess, doc, uid = _open_with_client(_BODY)

    # A whole-document state from an unrelated lineage — exactly what a
    # stale client syncs after a reseed it never heard about.
    foreign = markdown_yjs.seed_doc_from_markdown("POISON copy of the page\n")
    seq = coedit.apply_update(
        sess.id, update_bytes=foreign.get_update(), author_user_id=uid
    )
    assert seq is not None

    # Reseed-style bump that does NOT prune the poison row (seq=0 keeps it),
    # simulating a row that slipped into the pre-bump window.
    coedit.advance_checkpoint(
        sess.id,
        seq=0,
        snapshot=doc.get_update(),
        body=_BODY,
        base_sha=sess.base_sha or "",
        bump_lineage=True,
    )
    # A current-generation edit so the session is dirty and commits.
    coedit.apply_update(
        sess.id, update_bytes=_delta_edit(doc, "OK "), author_user_id=uid, expected_lineage=1
    )

    outcome = coedit_checkpoint.checkpoint_session(sess.id)

    assert outcome is not None
    committed = wiki_git.read_file(_PATH)
    assert "POISON" not in committed  # the stale row was skipped, not unioned
    assert committed.startswith("OK alpha one")
