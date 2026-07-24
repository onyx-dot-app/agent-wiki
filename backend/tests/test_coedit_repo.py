"""Co-editing session store (app/wiki/coedit.py) — get-or-create sessions,
the Yjs update log + checkpoint watermark, participant presence, and
lifecycle helpers (close/purge/move). DB-backed (uses the per-test schema
from the ``tmp_db`` fixture), mirroring ``test_comments_repo.py``.
"""
from __future__ import annotations

import os

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.db.models import CoeditSession
from app.db.session import session as db_session, try_advisory_xact_lock
from app.models.wiki import PathMove
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
    assert first.ydoc_checkpointed_seq == 0
    assert first.base_sha == "sha1"
    assert first.status == "active"

    # Second open on the same path adopts the existing session — the caller's
    # base_sha is ignored; the live room decides how to seed the doc.
    again = coedit.open_session(_PATH, base_sha="sha2")
    assert again.id == first.id
    assert again.base_sha == "sha1"
    assert count_rows(CoeditSession) == 1


def test_get_active_session(users):
    assert coedit.get_active_session(_PATH) is None
    opened = coedit.open_session(_PATH, base_sha=None)
    fetched = coedit.get_active_session(_PATH)
    assert fetched is not None
    assert fetched.id == opened.id


def test_get_session_returns_closed_too(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.close_session(s.id)
    assert coedit.get_active_session(_PATH) is None
    fetched = coedit.get_session(s.id)
    assert fetched is not None
    assert fetched.status == coedit.SessionStatus.CLOSED.value


# --------------------------------------------------------------------------- #
# Yjs update log + checkpoint watermark                                       #
# --------------------------------------------------------------------------- #


def test_ydoc_state_starts_empty(users):
    s = coedit.open_session(_PATH, base_sha="sha1")
    state = coedit.get_ydoc_state(s.id)
    assert state is not None
    assert state.snapshot is None
    assert state.seq == 0
    assert state.checkpointed_seq == 0
    assert state.base_sha == "sha1"


def test_ydoc_state_none_for_missing_session(users):
    assert coedit.get_ydoc_state(999999) is None


def test_append_ydoc_update_bumps_seq_and_logs(users):
    s = coedit.open_session(_PATH, base_sha=None)
    seq1 = coedit.append_ydoc_update(s.id, update_bytes=b"\x01\x02", author_user_id="usr_a")
    seq2 = coedit.append_ydoc_update(s.id, update_bytes=b"\x03\x04", author_user_id="usr_b")
    assert (seq1, seq2) == (1, 2)
    state = coedit.get_ydoc_state(s.id)
    assert state is not None
    assert state.seq == 2


def test_ydoc_updates_since_returns_ordered_tail(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.append_ydoc_update(s.id, update_bytes=b"a", author_user_id="usr_a")
    coedit.append_ydoc_update(s.id, update_bytes=b"b", author_user_id="usr_a")
    coedit.append_ydoc_update(s.id, update_bytes=b"c", author_user_id="usr_a")
    assert coedit.ydoc_updates_since(s.id, 0) == [b"a", b"b", b"c"]
    assert coedit.ydoc_updates_since(s.id, 1) == [b"b", b"c"]
    assert coedit.ydoc_updates_since(s.id, 3) == []


def test_checkpoint_ydoc_advances_watermark_and_snapshot(users):
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.append_ydoc_update(s.id, update_bytes=b"a", author_user_id="usr_a")
    coedit.append_ydoc_update(s.id, update_bytes=b"b", author_user_id="usr_a")
    ok = coedit.checkpoint_ydoc(s.id, snapshot=b"snap1", base_sha="sha2", seq=2)
    assert ok is True
    state = coedit.get_ydoc_state(s.id)
    assert state is not None
    assert state.checkpointed_seq == 2
    assert state.snapshot == b"snap1"
    assert state.base_sha == "sha2"


def test_checkpoint_ydoc_never_regresses(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.checkpoint_ydoc(s.id, snapshot=b"fast", base_sha="sha6", seq=6)
    # A slower concurrent checkpoint at a lower seq must not roll it back.
    regressed = coedit.checkpoint_ydoc(s.id, snapshot=b"slow", base_sha="sha5", seq=5)
    assert regressed is False
    state = coedit.get_ydoc_state(s.id)
    assert state is not None
    assert state.checkpointed_seq == 6
    assert state.snapshot == b"fast"


def _due_ids(**kw) -> set[int]:
    return {s.id for s in coedit.sessions_due_for_ydoc_checkpoint(**kw)}


def test_due_excludes_clean_session(users):
    s = coedit.open_session(_PATH, base_sha=None)
    assert _due_ids(idle_seconds=0, max_interval_seconds=0) == set()
    assert s.id not in _due_ids(idle_seconds=0, max_interval_seconds=0)


def test_due_includes_idle_dirty_session(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.append_ydoc_update(s.id, update_bytes=b"x", author_user_id="usr_a")
    assert s.id in _due_ids(idle_seconds=0, max_interval_seconds=3600)


def test_due_excludes_recently_edited_session(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.append_ydoc_update(s.id, update_bytes=b"x", author_user_id="usr_a")
    assert s.id not in _due_ids(idle_seconds=3600, max_interval_seconds=3600)


def test_due_includes_overdue_active_session(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.append_ydoc_update(s.id, update_bytes=b"x", author_user_id="usr_a")
    assert s.id in _due_ids(idle_seconds=3600, max_interval_seconds=0)


def test_due_excludes_session_clean_since_last_checkpoint(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.append_ydoc_update(s.id, update_bytes=b"x", author_user_id="usr_a")
    coedit.checkpoint_ydoc(s.id, snapshot=b"s", base_sha="sha", seq=1)
    assert _due_ids(idle_seconds=0, max_interval_seconds=0) == set()


def test_legacy_space_separated_created_at_normalized_not_overdue(users, monkeypatch):
    # A pre-migration row carries the space-separated server-default created_at.
    # Space sorts before 'T', so before normalization such a fresh, never-
    # checkpointed dirty session looks overdue against an _iso cutoff.
    monkeypatch.setattr(
        coedit, "_now", lambda: datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    )
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.append_ydoc_update(s.id, update_bytes=b"x", author_user_id="usr_a")
    with db_session() as sess:
        sess.execute(
            text(
                "UPDATE coedit_sessions "
                "SET created_at = replace(replace(created_at, 'T', ' '), '+00:00', '') "
                "WHERE id = :i"
            ),
            {"i": s.id},
        )
    assert s.id in _due_ids(idle_seconds=3600, max_interval_seconds=3600)

    with db_session() as sess:
        sess.execute(
            text(
                "UPDATE coedit_sessions SET created_at = replace(created_at, ' ', 'T') || '+00:00' "
                "WHERE position('T' in created_at) = 0"
            )
        )
    assert s.id not in _due_ids(idle_seconds=3600, max_interval_seconds=3600)


# --------------------------------------------------------------------------- #
# Lifecycle: close / purge / move                                            #
# --------------------------------------------------------------------------- #


def test_close_if_clean_closes_a_clean_session(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.append_ydoc_update(s.id, update_bytes=b"x", author_user_id="usr_a")
    coedit.checkpoint_ydoc(s.id, snapshot=b"s", base_sha="sha", seq=1)
    assert coedit.close_if_clean(s.id) is True
    assert coedit.get_active_session(_PATH) is None


def test_close_if_clean_skips_a_dirty_session(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.append_ydoc_update(s.id, update_bytes=b"x", author_user_id="usr_a")
    assert coedit.close_if_clean(s.id) is False
    active = coedit.get_active_session(_PATH)
    assert active is not None and active.id == s.id


def test_close_frees_path_for_new_session(users):
    first = coedit.open_session(_PATH, base_sha=None)
    coedit.close_session(first.id)
    second = coedit.open_session(_PATH, base_sha=None)
    assert second.id != first.id
    assert count_rows(CoeditSession) == 2
    active = coedit.get_active_session(_PATH)
    assert active is not None
    assert active.id == second.id


def test_close_session_with_participants_cascades(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.join(s.id, "usr_a")
    coedit.delete_for_path(_PATH)
    assert coedit.get_active_session(_PATH) is None
    assert count_rows(CoeditSession) == 0
    fresh = coedit.open_session(_PATH, base_sha=None)
    assert coedit.list_participants(fresh.id) == []


def test_purge_viewer_sessions_deletes_only_closed_never_edited(users):
    viewer_only = coedit.open_session("viewed.md", base_sha=None)
    coedit.join(viewer_only.id, "usr_a")
    coedit.leave(viewer_only.id, "usr_a")
    coedit.close_session(viewer_only.id)

    edited = coedit.open_session("edited.md", base_sha=None)
    coedit.append_ydoc_update(edited.id, update_bytes=b"x", author_user_id="usr_a")
    coedit.checkpoint_ydoc(edited.id, snapshot=b"s", base_sha="sha", seq=1)
    coedit.close_session(edited.id)

    active = coedit.open_session("open.md", base_sha=None)

    assert coedit.purge_viewer_sessions() == 1
    assert coedit.get_session(viewer_only.id) is None
    assert coedit.get_session(edited.id) is not None
    assert coedit.get_session(active.id) is not None
    assert coedit.purge_viewer_sessions() == 0


def test_rename_path(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.rename_path(_PATH, "guides/renamed.md")
    assert coedit.get_active_session(_PATH) is None
    moved = coedit.get_active_session("guides/renamed.md")
    assert moved is not None
    assert moved.id == s.id


def test_on_path_moved_rekeys_session(users):
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.join(s.id, "usr_a")

    new_path = "guides/install.md"
    coedit.on_path_moved([PathMove(old=_PATH, new=new_path)])

    assert coedit.get_active_session(_PATH) is None
    moved = coedit.get_active_session(new_path)
    assert moved is not None and moved.id == s.id


def test_on_path_moved_folder_rename_carries_sessions(users):
    s = coedit.open_session("a/deep/page.md", base_sha="sha1")

    coedit.on_path_moved([PathMove(old="a/deep/page.md", new="b/deep/page.md")])

    moved = coedit.get_active_session("b/deep/page.md")
    assert moved is not None and moved.id == s.id


def test_on_path_moved_leaves_siblings_alone(users):
    moved = coedit.open_session("a/deep/page.md", base_sha="sha1")
    sibling = coedit.open_session("a/deep/other.md", base_sha="sha2")

    coedit.on_path_moved([PathMove(old="a/deep/page.md", new="b/deep/page.md")])

    got_moved = coedit.get_active_session("b/deep/page.md")
    assert got_moved is not None and got_moved.id == moved.id
    got_sibling = coedit.get_active_session("a/deep/other.md")
    assert got_sibling is not None and got_sibling.id == sibling.id


def test_on_path_moved_origin_wins_over_young_dirty_destination(users):
    origin = coedit.open_session(_PATH, base_sha="sha1")
    young = coedit.open_session("guides/target.md", base_sha=None)
    coedit.append_ydoc_update(young.id, update_bytes=b"x", author_user_id="usr_a")

    coedit.on_path_moved([PathMove(old=_PATH, new="guides/target.md")])

    at_dest = coedit.get_active_session("guides/target.md")
    assert at_dest is not None and at_dest.id == origin.id
    superseded = coedit.get_session(young.id)
    assert superseded is not None
    assert superseded.status == coedit.SessionStatus.CLOSED.value


def test_on_path_moved_supersedes_clean_destination_session(users):
    origin = coedit.open_session(_PATH, base_sha="sha1")
    fresh = coedit.open_session("guides/target.md", base_sha="sha1")

    coedit.on_path_moved([PathMove(old=_PATH, new="guides/target.md")])

    at_dest = coedit.get_active_session("guides/target.md")
    assert at_dest is not None and at_dest.id == origin.id
    superseded = coedit.get_session(fresh.id)
    assert superseded is not None
    assert superseded.status == coedit.SessionStatus.CLOSED.value


def test_blocking_active_session_path(users):
    assert coedit.blocking_active_session_path("guides/new-home.md") is None
    coedit.open_session("guides/new-home.md", base_sha=None)
    assert coedit.blocking_active_session_path("guides/new-home.md") == "guides/new-home.md"
    assert coedit.blocking_active_session_path("guides") == "guides/new-home.md"
    assert coedit.blocking_active_session_path("other") is None


# --------------------------------------------------------------------------- #
# Participants                                                                #
# --------------------------------------------------------------------------- #


def test_participants_join_touch_leave(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.join(s.id, "usr_a")
    coedit.join(s.id, "usr_b")

    rows = coedit.list_participants(s.id)
    assert [r.user_id for r in rows] == ["usr_a", "usr_b"]
    assert {r.user_display for r in rows} == {"Ada", "Bo"}

    before = next(r for r in coedit.list_participants(s.id) if r.user_id == "usr_a")
    coedit.join(s.id, "usr_a")
    coedit.touch(s.id, "usr_a")
    after = next(r for r in coedit.list_participants(s.id) if r.user_id == "usr_a")
    assert after.last_seen_at >= before.last_seen_at
    assert len(coedit.list_participants(s.id)) == 2

    coedit.leave(s.id, "usr_a")
    assert [r.user_id for r in coedit.list_participants(s.id)] == ["usr_b"]


# --------------------------------------------------------------------------- #
# Checkpoint advisory lock                                                    #
# --------------------------------------------------------------------------- #


def test_checkpoint_lock_serializes_same_session_only(users):
    base = 1_000_000 + os.getpid() % 1_000_000
    sid, other = base, base + 1

    def try_lock(s, session_id: int) -> bool:
        return bool(
            s.execute(
                text("SELECT pg_try_advisory_xact_lock(:k)"),
                {"k": coedit.checkpoint_lock_key(session_id)},
            ).scalar()
        )

    with coedit.checkpoint_lock(sid) as acquired:
        assert acquired is True
        with db_session() as s2:
            assert try_lock(s2, sid) is False
            assert try_lock(s2, other) is True
    with db_session() as s3:
        assert try_lock(s3, sid) is True


def test_checkpoint_lock_times_out_when_held(users):
    base = 2_000_000 + os.getpid() % 1_000_000
    with coedit.checkpoint_lock(base) as acquired:
        assert acquired is True
        with db_session() as s2:
            got = try_advisory_xact_lock(s2, coedit.checkpoint_lock_key(base), timeout_ms=100)
        assert got is False
