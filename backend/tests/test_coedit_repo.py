"""Co-editing session store (app/wiki/coedit.py) — get-or-create sessions,
CAS on the buffer, participant presence, checkpoint + lifecycle helpers.

DB-backed (uses the per-test schema from the ``tmp_db`` fixture), mirroring
``test_comments_repo.py``.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sqlalchemy import text

from app.db.models import CoeditSession
from app.db.session import session as db_session
from app.wiki import coedit
from tests._seed import count_rows, seed_user

_PATH = "guides/setup.md"


def _ch(frm: int, to: int, insert: str = "") -> coedit.Change:
    # Build via model_validate so the aliased "from" field is set by its wire
    # name — Change(from_=...) isn't expressible (the constructor param is the
    # alias "from", a keyword).
    return coedit.Change.model_validate({"from": frm, "to": to, "insert": insert})


@pytest.fixture
def users(tmp_db):
    seed_user("usr_a", "a@x.com", name="Ada")
    seed_user("usr_b", "b@x.com", name="Bo")
    return tmp_db


def test_open_session_get_or_create(users):
    first = coedit.open_session(_PATH, base_sha="sha1", initial_buffer="hello")
    assert first.path == _PATH
    assert first.buffer_text == "hello"
    assert first.version == 0
    assert first.base_sha == "sha1"
    assert first.status == "active"

    # Second open on the same path adopts the existing session — the live
    # buffer wins, the new base_sha/initial_buffer are ignored.
    again = coedit.open_session(_PATH, base_sha="sha2", initial_buffer="ignored")
    assert again.id == first.id
    assert again.buffer_text == "hello"
    assert again.base_sha == "sha1"
    assert count_rows(CoeditSession) == 1


def test_get_active_session(users):
    assert coedit.get_active_session(_PATH) is None
    opened = coedit.open_session(_PATH, base_sha=None)
    fetched = coedit.get_active_session(_PATH)
    assert fetched is not None
    assert fetched.id == opened.id


def test_set_buffer_cas_success(users):
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="v0")
    updated = coedit.set_buffer(s.id, base_version=0, buffer_text="v1")
    assert updated is not None
    assert updated.version == 1
    assert updated.buffer_text == "v1"

    again = coedit.set_buffer(s.id, base_version=1, buffer_text="v2")
    assert again is not None
    assert again.version == 2
    assert again.buffer_text == "v2"


def test_set_buffer_cas_stale_is_rejected(users):
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="v0")
    assert coedit.set_buffer(s.id, base_version=0, buffer_text="v1") is not None

    # Caller still thinks it's on version 0 — reject, leave the buffer alone.
    stale = coedit.set_buffer(s.id, base_version=0, buffer_text="clobber")
    assert stale is None
    current = coedit.get_active_session(_PATH)
    assert current is not None
    assert current.version == 1
    assert current.buffer_text == "v1"


def test_set_buffer_concurrent_writers_one_wins(users):
    # Two writers both read version 0 and race to apply a patch. The atomic
    # conditional UPDATE means exactly one swap lands; the loser is rejected
    # (None) and must rebase. No lost update — the winner's text survives.
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="v0")
    first = coedit.set_buffer(s.id, base_version=0, buffer_text="from-A")
    second = coedit.set_buffer(s.id, base_version=0, buffer_text="from-B")

    winners = [r for r in (first, second) if r is not None]
    assert len(winners) == 1
    assert winners[0].version == 1
    current = coedit.get_active_session(_PATH)
    assert current is not None
    assert current.version == 1
    assert current.buffer_text == winners[0].buffer_text


def test_set_buffer_on_closed_session_returns_none(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.close_session(s.id)
    assert coedit.set_buffer(s.id, base_version=0, buffer_text="x") is None


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


def test_mark_checkpointed(users):
    s = coedit.open_session(_PATH, base_sha="sha1")
    coedit.mark_checkpointed(s.id, base_sha="sha2", version=3)
    fetched = coedit.get_active_session(_PATH)
    assert fetched is not None
    assert fetched.base_sha == "sha2"
    assert fetched.checkpointed_version == 3
    assert fetched.last_checkpoint_at is not None


def test_mark_checkpointed_never_regresses(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.mark_checkpointed(s.id, base_sha="sha6", version=6)
    # A slower concurrent checkpoint at a lower version must not roll it back.
    coedit.mark_checkpointed(s.id, base_sha="sha5", version=5)
    fetched = coedit.get_active_session(_PATH)
    assert fetched is not None
    assert fetched.checkpointed_version == 6
    assert fetched.base_sha == "sha6"


def _due_ids(**kw) -> set[int]:
    return {s.id for s in coedit.sessions_due_for_checkpoint(**kw)}


def test_due_excludes_clean_session(users):
    # Never edited (version == checkpointed_version) → never a checkpoint
    # candidate, even with zero cutoffs.
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="x")
    assert _due_ids(idle_seconds=0, max_interval_seconds=0) == set()
    assert s.id not in _due_ids(idle_seconds=0, max_interval_seconds=0)


def test_due_includes_idle_dirty_session(users):
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="hi")
    coedit.apply_op(s.id, base_version=0, changes=[_ch(0, 2, "yo")], author_user_id="usr_a")
    # idle_seconds=0 → any past edit counts as settled; max_interval large so
    # the idle branch alone is what selects it.
    assert s.id in _due_ids(idle_seconds=0, max_interval_seconds=3600)


def test_due_excludes_recently_edited_session(users):
    # Dirty but just edited and never checkpointed: not idle, and not overdue
    # (measured from session start) → not grabbed mid-typing.
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="hi")
    coedit.apply_op(s.id, base_version=0, changes=[_ch(0, 2, "yo")], author_user_id="usr_a")
    assert s.id not in _due_ids(idle_seconds=3600, max_interval_seconds=3600)


def test_due_includes_overdue_active_session(users):
    # Still actively edited (not idle) but past the max interval since session
    # start → forced so a never-idle session can't stay uncommitted forever.
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="hi")
    coedit.apply_op(s.id, base_version=0, changes=[_ch(0, 2, "yo")], author_user_id="usr_a")
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
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="hi")
    coedit.apply_op(s.id, base_version=0, changes=[_ch(0, 2, "yo")], author_user_id="usr_a")
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
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="hi")
    coedit.apply_op(s.id, base_version=0, changes=[_ch(0, 2, "yo")], author_user_id="usr_a")
    coedit.mark_checkpointed(s.id, base_sha="sha", version=1)
    # version == checkpointed_version again → no longer dirty.
    assert _due_ids(idle_seconds=0, max_interval_seconds=0) == set()


def test_close_if_clean_closes_a_clean_session(users):
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="hi")
    coedit.apply_op(s.id, base_version=0, changes=[_ch(0, 2, "yo")], author_user_id="usr_a")
    coedit.mark_checkpointed(s.id, base_sha="sha", version=1)  # version == checkpointed
    assert coedit.close_if_clean(s.id) is True
    assert coedit.get_active_session(_PATH) is None


def test_close_if_clean_skips_a_dirty_session(users):
    # A late op after the checkpoint (version > checkpointed_version) must not be
    # sealed in a closed session — close_if_clean leaves it active for the scan.
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="hi")
    coedit.apply_op(s.id, base_version=0, changes=[_ch(0, 2, "yo")], author_user_id="usr_a")
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


def test_close_session_with_participants_cascades(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.join(s.id, "usr_a")
    coedit.delete_for_path(_PATH)
    assert coedit.get_active_session(_PATH) is None
    assert count_rows(CoeditSession) == 0
    # Participant rows cascade with the session.
    fresh = coedit.open_session(_PATH, base_sha=None)
    assert coedit.list_participants(fresh.id) == []


def test_rename_path(users):
    s = coedit.open_session(_PATH, base_sha=None)
    coedit.rename_path(_PATH, "guides/renamed.md")
    assert coedit.get_active_session(_PATH) is None
    moved = coedit.get_active_session("guides/renamed.md")
    assert moved is not None
    assert moved.id == s.id


# --------------------------------------------------------------------------- #
# apply_op — range-change application + version CAS + op log                   #
# --------------------------------------------------------------------------- #


def test_apply_op_applies_change_bumps_version_and_logs(users):
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="hello world")
    out = coedit.apply_op(s.id, base_version=0, changes=[_ch(0, 5, "hi")], author_user_id="usr_a")
    assert out is not None
    assert out.version == 1
    assert out.buffer_text == "hi world"
    logged = coedit.ops_since(s.id, 0)
    assert [o.seq for o in logged] == [1]
    assert logged[0].base_version == 0
    assert logged[0].author_user_id == "usr_a"
    assert logged[0].changes == [{"from": 0, "to": 5, "insert": "hi"}]


def test_apply_op_multiple_changes_apply_right_to_left(users):
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="abcdef")
    out = coedit.apply_op(
        s.id, base_version=0, changes=[_ch(0, 1, "X"), _ch(4, 6, "Y")], author_user_id="usr_a"
    )
    assert out is not None
    assert out.buffer_text == "XbcdY"


def test_apply_op_stale_base_version_rejected(users):
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="v0")
    assert coedit.apply_op(s.id, base_version=0, changes=[_ch(2, 2, "!")], author_user_id="usr_a") is not None
    # Caller still on version 0 — rejected, buffer untouched.
    stale = coedit.apply_op(s.id, base_version=0, changes=[_ch(0, 0, "X")], author_user_id="usr_a")
    assert stale is None
    current = coedit.get_active_session(_PATH)
    assert current is not None
    assert current.version == 1
    assert current.buffer_text == "v0!"


def test_apply_op_out_of_bounds_raises(users):
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="short")
    with pytest.raises(ValueError):
        coedit.apply_op(s.id, base_version=0, changes=[_ch(0, 999, "x")], author_user_id="usr_a")


def test_apply_op_concurrent_one_wins(users):
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="v0")
    a = coedit.apply_op(s.id, base_version=0, changes=[_ch(0, 0, "A")], author_user_id="usr_a")
    b = coedit.apply_op(s.id, base_version=0, changes=[_ch(0, 0, "B")], author_user_id="usr_b")
    winners = [r for r in (a, b) if r is not None]
    assert len(winners) == 1
    assert winners[0].version == 1


def test_apply_op_rejects_overlapping_changes(users):
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="abcdef")
    with pytest.raises(ValueError):
        coedit.apply_op(
            s.id, base_version=0, changes=[_ch(0, 3, "X"), _ch(2, 4, "Y")], author_user_id="usr_a"
        )


def test_change_rejects_malformed_input():
    # Missing 'to' / negative offset are rejected at the type boundary
    # (pydantic ValidationError is a ValueError subclass).
    with pytest.raises(ValueError):
        coedit.Change.model_validate({"from": 0, "insert": "x"})
    with pytest.raises(ValueError):
        coedit.Change.model_validate({"from": -1, "to": 0})


def test_apply_op_uses_utf16_offsets_with_emoji(users):
    # 'a'=unit 0, 😀=units 1-2 (astral → 2 UTF-16 units), 'b'=unit 3.
    # Replacing [3,4) must hit 'b' → "a😀!". Code-point slicing (len 3) would
    # instead append, giving "a😀b!", so this pins the UTF-16 contract.
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="a😀b")
    out = coedit.apply_op(s.id, base_version=0, changes=[_ch(3, 4, "!")], author_user_id="usr_a")
    assert out is not None
    assert out.buffer_text == "a😀!"


def test_apply_op_can_insert_astral_char(users):
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="ab")
    out = coedit.apply_op(s.id, base_version=0, changes=[_ch(1, 1, "🎉")], author_user_id="usr_a")
    assert out is not None
    assert out.buffer_text == "a🎉b"


def test_ops_since_returns_ops_after_version(users):
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="")
    coedit.apply_op(s.id, base_version=0, changes=[_ch(0, 0, "a")], author_user_id="usr_a")
    coedit.apply_op(s.id, base_version=1, changes=[_ch(1, 1, "b")], author_user_id="usr_a")
    assert [o.seq for o in coedit.ops_since(s.id, 0)] == [1, 2]
    assert [o.seq for o in coedit.ops_since(s.id, 1)] == [2]
    assert coedit.ops_since(s.id, 2) == []
