"""Co-editing session store (app/wiki/coedit.py) — get-or-create sessions,
CAS on the buffer, participant presence, checkpoint + lifecycle helpers.

DB-backed (uses the per-test schema from the ``tmp_db`` fixture), mirroring
``test_comments_repo.py``.
"""
from __future__ import annotations

import pytest

from app.db.models import CoeditSession
from app.wiki import coedit
from tests._seed import count_rows, seed_user

_PATH = "guides/setup.md"


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
    coedit.mark_checkpointed(s.id, base_sha="sha2")
    fetched = coedit.get_active_session(_PATH)
    assert fetched is not None
    assert fetched.base_sha == "sha2"
    assert fetched.last_checkpoint_at is not None


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
    out = coedit.apply_op(
        s.id,
        base_version=0,
        changes=[{"from": 0, "to": 5, "insert": "hi"}],
        author_user_id="usr_a",
    )
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
        s.id,
        base_version=0,
        changes=[
            {"from": 0, "to": 1, "insert": "X"},
            {"from": 4, "to": 6, "insert": "Y"},
        ],
        author_user_id="usr_a",
    )
    assert out is not None
    assert out.buffer_text == "XbcdY"


def test_apply_op_stale_base_version_rejected(users):
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="v0")
    assert coedit.apply_op(
        s.id, base_version=0, changes=[{"from": 2, "to": 2, "insert": "!"}], author_user_id="usr_a"
    ) is not None
    # Caller still on version 0 — rejected, buffer untouched.
    stale = coedit.apply_op(
        s.id, base_version=0, changes=[{"from": 0, "to": 0, "insert": "X"}], author_user_id="usr_a"
    )
    assert stale is None
    current = coedit.get_active_session(_PATH)
    assert current is not None
    assert current.version == 1
    assert current.buffer_text == "v0!"


def test_apply_op_out_of_bounds_raises(users):
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="short")
    with pytest.raises(ValueError):
        coedit.apply_op(
            s.id, base_version=0, changes=[{"from": 0, "to": 999, "insert": "x"}], author_user_id="usr_a"
        )


def test_apply_op_concurrent_one_wins(users):
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="v0")
    a = coedit.apply_op(s.id, base_version=0, changes=[{"from": 0, "to": 0, "insert": "A"}], author_user_id="usr_a")
    b = coedit.apply_op(s.id, base_version=0, changes=[{"from": 0, "to": 0, "insert": "B"}], author_user_id="usr_b")
    winners = [r for r in (a, b) if r is not None]
    assert len(winners) == 1
    assert winners[0].version == 1


def test_apply_op_uses_utf16_offsets_with_emoji(users):
    # 'a'=unit 0, 😀=units 1-2 (astral → 2 UTF-16 units), 'b'=unit 3.
    # Replacing [3,4) must hit 'b' → "a😀!". Code-point slicing (len 3) would
    # instead append, giving "a😀b!", so this pins the UTF-16 contract.
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="a😀b")
    out = coedit.apply_op(
        s.id, base_version=0, changes=[{"from": 3, "to": 4, "insert": "!"}], author_user_id="usr_a"
    )
    assert out is not None
    assert out.buffer_text == "a😀!"


def test_apply_op_can_insert_astral_char(users):
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="ab")
    out = coedit.apply_op(
        s.id, base_version=0, changes=[{"from": 1, "to": 1, "insert": "🎉"}], author_user_id="usr_a"
    )
    assert out is not None
    assert out.buffer_text == "a🎉b"


def test_ops_since_returns_ops_after_version(users):
    s = coedit.open_session(_PATH, base_sha=None, initial_buffer="")
    coedit.apply_op(s.id, base_version=0, changes=[{"from": 0, "to": 0, "insert": "a"}], author_user_id="usr_a")
    coedit.apply_op(s.id, base_version=1, changes=[{"from": 1, "to": 1, "insert": "b"}], author_user_id="usr_a")
    assert [o.seq for o in coedit.ops_since(s.id, 0)] == [1, 2]
    assert [o.seq for o in coedit.ops_since(s.id, 1)] == [2]
    assert coedit.ops_since(s.id, 2) == []
