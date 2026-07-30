"""Co-editing session store (app/wiki/coedit.py) — get-or-create sessions,
the Yjs update log, participant presence, checkpoint + lifecycle helpers.

DB-backed (uses the per-test schema from the ``tmp_db`` fixture), mirroring
``test_comments_repo.py``. Pure DB bookkeeping only — this module never
imports ``pycrdt`` (see its own docstring), so these tests don't need a real
``Doc``/room; update payloads are opaque bytes as far as this layer cares.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from sqlalchemy import text, update

from app.db.models import CoeditParticipant, CoeditSession
from app.db.session import session as db_session
from app.wiki import coedit
from tests._seed import count_rows, seed_user

_PATH = "guides/setup.md"


@pytest.fixture
def users(tmp_db):
    seed_user("usr_a", "a@x.com", name="Ada")
    seed_user("usr_b", "b@x.com", name="Bo")
    return tmp_db


def test_open_session_get_or_create(users):
    first = coedit.open_session(_PATH, base_sha="sha1")
    assert first.path == _PATH
    assert first.ydoc_seq == 0
    assert first.base_sha == "sha1"
    assert first.status == "active"

    # Second open on the same path adopts the existing session — the live
    # room wins, the new base_sha is ignored.
    again = coedit.open_session(_PATH, base_sha="sha2")
    assert again.id == first.id
    assert again.base_sha == "sha1"
    assert count_rows(CoeditSession) == 1


def test_set_initial_snapshot(users):
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s.id, b"snap0", "hello")
    row = coedit.get_session_for_checkpoint(s.id)
    assert row is not None
    assert row.ydoc_snapshot == b"snap0"
    assert row.ydoc_snapshot_seq == 0
    assert row.ydoc_snapshot_body == "hello"


def test_set_initial_snapshot_only_writes_once(users):
    # A second process creating its own room for a session that already has
    # one (a checkpoint already ran, or another process's connection
    # already stamped one) must not clobber the existing snapshot.
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.set_initial_snapshot(s.id, b"first", "one")
    coedit.set_initial_snapshot(s.id, b"second", "two")
    row = coedit.get_session_for_checkpoint(s.id)
    assert row is not None
    assert row.ydoc_snapshot == b"first"
    assert row.ydoc_snapshot_body == "one"


def test_get_active_session(users):
    assert coedit.get_active_session(_PATH) is None
    opened = coedit.open_session(_PATH, base_sha=None)
    fetched = coedit.get_active_session(_PATH)
    assert fetched is not None
    assert fetched.id == opened.id


def test_apply_update_logs_and_bumps_seq(users):
    s = coedit.open_session(_PATH, base_sha=None)
    seq = coedit.apply_update(s.id, update_bytes=b"\x00\x01update-1", author_user_id="usr_a")
    assert seq == 1
    fetched = coedit.get_active_session(_PATH)
    assert fetched is not None
    assert fetched.ydoc_seq == 1

    seq2 = coedit.apply_update(s.id, update_bytes=b"\x00\x01update-2", author_user_id="usr_b")
    assert seq2 == 2
    fetched = coedit.get_active_session(_PATH)
    assert fetched is not None
    assert fetched.ydoc_seq == 2


def test_apply_update_both_concurrent_writers_land(users):
    # Unlike the OT era's version-CAS, CRDT updates don't reject on a stale
    # base — both apply, sequentially numbered. Conflict resolution already
    # happened at the pycrdt.Doc level before this is ever called.
    s = coedit.open_session(_PATH, base_sha=None)
    a = coedit.apply_update(s.id, update_bytes=b"A", author_user_id="usr_a")
    b = coedit.apply_update(s.id, update_bytes=b"B", author_user_id="usr_b")
    assert {a, b} == {1, 2}


def test_apply_update_on_closed_session_returns_none(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.close_session(s.id)
    assert coedit.apply_update(s.id, update_bytes=b"x", author_user_id="usr_a") is None


def test_updates_since_returns_updates_after_seq(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.apply_update(s.id, update_bytes=b"a", author_user_id="usr_a", client_id="cli_1")
    coedit.apply_update(s.id, update_bytes=b"b", author_user_id="usr_a")
    assert [u.seq for u in coedit.updates_since(s.id, 0).updates] == [1, 2]
    assert [u.seq for u in coedit.updates_since(s.id, 1).updates] == [2]
    assert coedit.updates_since(s.id, 2).updates == []
    # Head seq comes back alongside the updates, from one consistent read.
    result = coedit.updates_since(s.id, 0)
    assert result.head_seq == 2
    assert result.updates[0].update_payload == b"a"
    assert result.updates[0].client_id == "cli_1"
    assert result.updates[0].author_user_id == "usr_a"


def test_updates_since_gone_session_returns_none_head(users):
    assert coedit.updates_since(999999, 0) == coedit.UpdatesSince(head_seq=None, updates=[])


def test_last_update_author_returns_most_recent(users):
    s = coedit.open_session(_PATH, base_sha=None)
    assert coedit.last_update_author(s.id) is None
    coedit.apply_update(s.id, update_bytes=b"a", author_user_id="usr_a")
    coedit.apply_update(s.id, update_bytes=b"b", author_user_id="usr_b")
    assert coedit.last_update_author(s.id) == "usr_b"


def test_rebase_onto_bumps_seq_and_clears_updates(users):
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.apply_update(s.id, update_bytes=b"a", author_user_id="usr_a")
    res = coedit.rebase_onto(
        s.id, new_base_sha="sha2", snapshot=b"snap", body="body", expected_seq=1, checkpointed=False
    )
    assert res is not None
    assert res.ydoc_seq == 2
    assert res.base_sha == "sha2"
    assert res.ydoc_checkpointed_seq == 0  # checkpointed=False leaves the watermark alone
    assert coedit.updates_since(s.id, 0).updates == []
    # The snapshot moves with the rebase, to the new ydoc_seq — otherwise a
    # later checkpoint would rebuild from a stale snapshot plus the
    # now-empty update log and drop everything since the last advance.
    row = coedit.get_session_for_checkpoint(s.id)
    assert row is not None
    assert row.ydoc_snapshot == b"snap"
    assert row.ydoc_snapshot_seq == 2


def test_rebase_onto_checkpointed_advances_watermark(users):
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.apply_update(s.id, update_bytes=b"a", author_user_id="usr_a")
    res = coedit.rebase_onto(
        s.id, new_base_sha="sha2", snapshot=b"snap", body="body", expected_seq=1, checkpointed=True
    )
    assert res is not None
    assert res.ydoc_checkpointed_seq == res.ydoc_seq
    assert res.last_checkpoint_at is not None


def test_rebase_onto_closed_session_returns_none(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.close_session(s.id)
    assert (
        coedit.rebase_onto(
            s.id, new_base_sha="sha2", snapshot=b"snap", body="body", expected_seq=0, checkpointed=False
        )
        is None
    )


def test_rebase_onto_seq_mismatch_returns_none(users):
    # A concurrent edit bumped ydoc_seq past what the caller observed when it
    # built snapshot/body — same CAS-miss shape as a closed session: the
    # rebase must no-op rather than clobber that edit's log row/content (the
    # bug this test guards: main's OT-era rebase had a base_version CAS for
    # exactly this race, lost in the CRDT rewrite until restored).
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.apply_update(s.id, update_bytes=b"a", author_user_id="usr_a")
    assert (
        coedit.rebase_onto(
            s.id, new_base_sha="sha2", snapshot=b"snap", body="body", expected_seq=0, checkpointed=False
        )
        is None
    )
    # Nothing observable changed: the concurrent edit's row/watermark survive.
    fetched = coedit.get_active_session(_PATH)
    assert fetched is not None
    assert fetched.base_sha == "sha1"
    assert fetched.ydoc_seq == 1
    assert len(coedit.updates_since(s.id, 0).updates) == 1


def test_rebase_onto_returns_none_when_checkpoint_lock_busy(users, monkeypatch):
    # Regression test (review): rebase_onto used to write directly, with
    # no checkpoint_lock at all — a concurrent rebase_onto/checkpoint could
    # land its own delete-all-then-advance between a reader's two separate
    # reads (get_session_for_checkpoint then updates_since), yielding a
    # stale snapshot paired with an already-pruned update log. Taking the
    # lock closes that; this pins the "can't get it" half specifically —
    # a busy lock must make the whole call a no-op (same as a CAS miss),
    # never proceed unprotected.
    @contextmanager
    def fake_busy_lock(session_id: int, *, timeout_ms: int | None = None):
        yield False

    monkeypatch.setattr(coedit, "checkpoint_lock", fake_busy_lock)
    s = coedit.open_session(_PATH, base_sha="sha1")
    assert (
        coedit.rebase_onto(
            s.id, new_base_sha="sha2", snapshot=b"snap", body="body", expected_seq=0, checkpointed=False
        )
        is None
    )
    # Nothing changed — the whole write was skipped, not partially applied.
    fetched = coedit.get_active_session(_PATH)
    assert fetched is not None
    assert fetched.base_sha == "sha1"
    assert fetched.ydoc_seq == 0


def test_participants_join_touch_leave(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.join(s.id, "usr_a")
    coedit.join(s.id, "usr_b")

    rows = coedit.list_participants(s.id)
    assert [r.user_id for r in rows] == ["usr_a", "usr_b"]
    assert {r.user_display for r in rows} == {"Ada", "Bo"}

    # join on an existing participant is idempotent (refreshes last_seen, no
    # duplicate row); touch does the same without re-adding.
    before = next(r for r in coedit.list_participants(s.id) if r.user_id == "usr_a")
    coedit.join(s.id, "usr_a")
    coedit.touch(s.id, "usr_a")
    after = next(r for r in coedit.list_participants(s.id) if r.user_id == "usr_a")
    assert after.last_seen_at >= before.last_seen_at
    assert len(coedit.list_participants(s.id)) == 2

    coedit.leave(s.id, "usr_a")
    assert [r.user_id for r in coedit.list_participants(s.id)] == ["usr_b"]


def _make_participant_stale(session_id: int, user_id: str) -> None:
    with db_session() as db:
        db.execute(
            update(CoeditParticipant)
            .where(
                CoeditParticipant.session_id == session_id,
                CoeditParticipant.user_id == user_id,
            )
            .values(last_seen_at="2000-01-01T00:00:00+00:00")
        )


def test_stale_participant_expiry_keeps_live_peer(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.join(s.id, "usr_a")
    coedit.join(s.id, "usr_b")
    _make_participant_stale(s.id, "usr_a")

    expired = coedit.expire_stale_participants(stale_seconds=60)
    assert expired.changed_session_ids == [s.id]
    assert expired.empty_session_ids == []
    assert [p.user_id for p in coedit.list_participants(s.id)] == ["usr_b"]
    assert coedit.touch(s.id, "usr_b") is True


def test_stale_participant_expiry_reports_empty_session(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.join(s.id, "usr_a")
    _make_participant_stale(s.id, "usr_a")

    expired = coedit.expire_stale_participants(stale_seconds=60)
    assert expired.changed_session_ids == [s.id]
    assert expired.empty_session_ids == [s.id]
    assert coedit.list_participants(s.id) == []
    assert coedit.touch(s.id, "usr_a") is False


def test_join_rejects_session_closed_during_connect(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.close_session(s.id)
    assert coedit.join(s.id, "usr_a") is False
    assert coedit.list_participants(s.id) == []


def test_advance_checkpoint(users):
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.apply_update(s.id, update_bytes=b"a", author_user_id="usr_a")
    coedit.apply_update(s.id, update_bytes=b"b", author_user_id="usr_a")
    coedit.advance_checkpoint(s.id, seq=2, snapshot=b"snap", body="body", base_sha="sha2")
    fetched = coedit.get_active_session(_PATH)
    assert fetched is not None
    assert fetched.base_sha == "sha2"
    assert fetched.ydoc_checkpointed_seq == 2
    assert fetched.last_checkpoint_at is not None
    # Pruned everything <= 2 — both updates gone.
    assert coedit.updates_since(s.id, 0).updates == []
    checkpoint_row = coedit.get_session_for_checkpoint(s.id)
    assert checkpoint_row is not None
    assert checkpoint_row.ydoc_snapshot == b"snap"
    assert checkpoint_row.ydoc_snapshot_seq == 2


def test_advance_checkpoint_never_regresses(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.advance_checkpoint(s.id, seq=6, snapshot=b"snap6", body="body", base_sha="sha6")
    # A slower concurrent checkpoint at a lower seq must not roll it back.
    coedit.advance_checkpoint(s.id, seq=5, snapshot=b"snap5", body="body", base_sha="sha5")
    fetched = coedit.get_active_session(_PATH)
    assert fetched is not None
    assert fetched.ydoc_checkpointed_seq == 6
    assert fetched.base_sha == "sha6"
    checkpoint_row = coedit.get_session_for_checkpoint(s.id)
    assert checkpoint_row is not None
    assert checkpoint_row.ydoc_snapshot == b"snap6"  # not regressed either


def test_advance_checkpoint_prunes_only_up_to_seq(users):
    # A later update (one that landed after this checkpoint's own read of
    # the log) must survive pruning — exactly the bug the old
    # rebase_onto(checkpointed=True)'s unconditional delete had: pruning and
    # the snapshot watermark have to move in lockstep, never past what was
    # actually captured.
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.apply_update(s.id, update_bytes=b"a", author_user_id="usr_a")  # seq 1
    coedit.advance_checkpoint(s.id, seq=1, snapshot=b"snap1", body="body", base_sha="sha2")
    # Landed after this checkpoint's own read of the log.
    coedit.apply_update(s.id, update_bytes=b"b", author_user_id="usr_a")  # seq 2
    remaining = coedit.updates_since(s.id, 0).updates
    assert [u.seq for u in remaining] == [2]


def _due_ids(**kw) -> set[int]:
    return {s.id for s in coedit.sessions_due_for_checkpoint(**kw)}


def test_due_excludes_clean_session(users):
    # Never edited (ydoc_seq == ydoc_checkpointed_seq) → never a checkpoint
    # candidate, even with zero cutoffs.
    s = coedit.open_session(_PATH, base_sha=None)
    assert _due_ids(idle_seconds=0, max_interval_seconds=0) == set()
    assert s.id not in _due_ids(idle_seconds=0, max_interval_seconds=0)


def test_due_includes_idle_dirty_session(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.apply_update(s.id, update_bytes=b"yo", author_user_id="usr_a")
    # idle_seconds=0 → any past edit counts as settled; max_interval large so
    # the idle branch alone is what selects it.
    assert s.id in _due_ids(idle_seconds=0, max_interval_seconds=3600)


def test_due_excludes_recently_edited_session(users):
    # Dirty but just edited and never checkpointed: not idle, and not overdue
    # (measured from session start) → not grabbed mid-typing.
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.apply_update(s.id, update_bytes=b"yo", author_user_id="usr_a")
    assert s.id not in _due_ids(idle_seconds=3600, max_interval_seconds=3600)


def test_due_includes_overdue_active_session(users):
    # Still actively edited (not idle) but past the max interval since session
    # start → forced so a never-idle session can't stay uncommitted forever.
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.apply_update(s.id, update_bytes=b"yo", author_user_id="usr_a")
    assert s.id in _due_ids(idle_seconds=3600, max_interval_seconds=0)


def test_legacy_space_separated_created_at_normalized_not_overdue(users, monkeypatch):
    # A pre-migration row carries the space-separated server-default created_at.
    # Space sorts before 'T', so before normalization such a fresh, never-
    # checkpointed dirty session looks overdue against an _iso cutoff.
    #
    # Freeze _now at midday UTC so created_at and the (now - max_interval) cutoff
    # share the same date+hour prefix — otherwise, when real wall-clock is within
    # max_interval of a day/hour boundary, the earlier date digits dominate the
    # lexicographic compare instead of the space-vs-'T' at position 10.
    monkeypatch.setattr(
        coedit, "_now", lambda: datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    )
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.apply_update(s.id, update_bytes=b"yo", author_user_id="usr_a")
    with db_session() as sess:
        sess.execute(
            text(
                "UPDATE coedit_sessions "
                "SET created_at = replace(replace(created_at, 'T', ' '), '+00:00', '') "
                "WHERE id = :i"
            ),
            {"i": s.id},
        )
    # Documents the bug the migration fixes: falsely overdue while well inside
    # the max interval.
    assert s.id in _due_ids(idle_seconds=3600, max_interval_seconds=3600)

    # Migration 0042's normalization.
    with db_session() as sess:
        sess.execute(
            text(
                "UPDATE coedit_sessions SET created_at = replace(created_at, ' ', 'T') || '+00:00' "
                "WHERE position('T' in created_at) = 0"
            )
        )
    assert s.id not in _due_ids(idle_seconds=3600, max_interval_seconds=3600)


def test_due_excludes_session_clean_since_last_checkpoint(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.apply_update(s.id, update_bytes=b"yo", author_user_id="usr_a")
    coedit.advance_checkpoint(s.id, seq=1, snapshot=b"snap", body="body", base_sha="sha")
    # ydoc_seq == ydoc_checkpointed_seq again → no longer dirty.
    assert _due_ids(idle_seconds=0, max_interval_seconds=0) == set()


def test_close_if_clean_closes_a_clean_session(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.apply_update(s.id, update_bytes=b"yo", author_user_id="usr_a")
    coedit.advance_checkpoint(s.id, seq=1, snapshot=b"snap", body="body", base_sha="sha")  # ydoc_seq == checkpointed
    assert coedit.close_if_clean(s.id) is True
    assert coedit.get_active_session(_PATH) is None


def test_close_if_clean_skips_session_with_participant(users):
    s = coedit.open_session(_PATH, base_sha=None)
    assert coedit.join(s.id, "usr_a") is True
    assert coedit.close_if_clean(s.id) is False
    active = coedit.get_active_session(_PATH)
    assert active is not None and active.id == s.id


def test_close_if_clean_skips_a_dirty_session(users):
    # A late update after the checkpoint (ydoc_seq > ydoc_checkpointed_seq)
    # must not be sealed in a closed session — close_if_clean leaves it
    # active for the scan.
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.apply_update(s.id, update_bytes=b"yo", author_user_id="usr_a")
    assert coedit.close_if_clean(s.id) is False
    active = coedit.get_active_session(_PATH)
    assert active is not None and active.id == s.id


def test_close_frees_path_for_new_session(users):
    first = coedit.open_session(_PATH, base_sha=None)
    coedit.close_session(first.id)
    # Closing frees the path: a new active session can open (partial unique
    # index only constrains active rows), and the closed one is retained.
    second = coedit.open_session(_PATH, base_sha=None)
    assert second.id != first.id
    assert count_rows(CoeditSession) == 2
    active = coedit.get_active_session(_PATH)
    assert active is not None
    assert active.id == second.id


def test_rename_path(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.rename_path(_PATH, "guides/renamed.md")
    assert coedit.get_active_session(_PATH) is None
    moved = coedit.get_active_session("guides/renamed.md")
    assert moved is not None
    assert moved.id == s.id


def test_purge_viewer_sessions_deletes_only_closed_never_edited(users):
    # Viewer-only session: opened, no updates, closed.
    viewer_only = coedit.open_session("viewed.md", base_sha=None)
    coedit.join(viewer_only.id, "usr_a")
    coedit.leave(viewer_only.id, "usr_a")
    coedit.close_session(viewer_only.id)

    # Edited session: has an update, closed after checkpoint — must be
    # retained (ydoc_seq != 0, regardless of whether advance_checkpoint has
    # since pruned its coedit_updates rows).
    edited = coedit.open_session("edited.md", base_sha=None)
    coedit.apply_update(edited.id, update_bytes=b"x", author_user_id="usr_a")
    coedit.advance_checkpoint(edited.id, seq=1, snapshot=b"snap", body="body", base_sha="sha")
    coedit.close_session(edited.id)

    # Active viewer-only session: still occupied — must be retained.
    active = coedit.open_session("open.md", base_sha=None)

    assert coedit.purge_viewer_sessions() == 1
    assert coedit.get_session(viewer_only.id) is None
    assert coedit.get_session(edited.id) is not None
    assert coedit.get_session(active.id) is not None
    # Idempotent: nothing left to purge.
    assert coedit.purge_viewer_sessions() == 0


def test_close_abandoned_sessions_closes_only_clean_empty_ones(users):
    # Ported from the OT era: "clean" is now ydoc_seq == ydoc_checkpointed_seq.
    # This is the sweep that keeps the invariant "an active session has
    # participants" self-healing — a session emptied by any route other than the
    # expiry scan is invisible to that path, and being clean the checkpoint scan
    # skips it too, so without this it stays active forever holding the
    # active-path unique index.
    abandoned = coedit.open_session("abandoned.md", base_sha=None)
    occupied = coedit.open_session("occupied.md", base_sha=None)
    assert coedit.join(occupied.id, "usr_a") is True
    dirty = coedit.open_session("dirty.md", base_sha=None)
    coedit.apply_update(dirty.id, update_bytes=b"u", author_user_id="usr_a")

    assert coedit.close_abandoned_sessions() == [abandoned.id]
    assert coedit.get_active_session("abandoned.md") is None
    assert coedit.get_active_session("occupied.md") is not None  # presence holds it open
    assert coedit.get_active_session("dirty.md") is not None  # the checkpoint scan owns it
    assert coedit.close_abandoned_sessions() == []  # idempotent
