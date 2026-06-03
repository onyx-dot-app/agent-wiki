"""End-to-end tests for schedule-kind triggers.

Covers repo validation, ``find_due_schedule_triggers`` selection, the
periodic evaluator wiring, and the start-at anchor. The NL gate is
patched at the seam (``app.llm.client.complete``) — tests assert on
events rows the evaluator writes, not on prompt internals.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from tests._seed import list_events, seed_trigger, seed_user


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class _FakeToolCall(BaseModel):
    name: str
    arguments: dict


class _FakeResp(BaseModel):
    text: str = ""
    tool_calls: list[_FakeToolCall] = []
    stop_reason: str = "end_turn"
    usage: dict = {}


def _record(**overrides):
    from app.triggers.engine import TriggerRecord

    base = TriggerRecord(
        id="trg_x",
        owner_user_id="usr_x",
        scope_path="a.md",
        kind="schedule",
        nl_description="x",
        message="m",
        destination="event_log",
        enabled=True,
        file_path=None,
        created_at=None,
        last_edited_at=None,
        schedule_cron="0 * * * *",
        schedule_timezone="UTC",
    )
    return base.model_copy(update=overrides)


def _matched_response(matched: bool, reason: str = "test reason") -> _FakeResp:
    return _FakeResp(
        tool_calls=[
            _FakeToolCall(name="report", arguments={"matches": matched, "reason": reason}),
        ]
    )


def _rendered_response(message: str = "rendered text") -> _FakeResp:
    return _FakeResp(
        tool_calls=[
            _FakeToolCall(name="render", arguments={"message": message}),
        ]
    )


# --------------------------------------------------------------------------- #
# Repo validation                                                             #
# --------------------------------------------------------------------------- #


def test_create_schedule_requires_cron_and_tz(tmp_repo):
    from app.triggers import repo

    uid = seed_user(email="a@b.com")

    with pytest.raises(ValueError, match="schedule_cron is required"):
        repo.create(
            owner_user_id=uid,
            scope_path="a.md",
            nl_description="something",
            message="hi",
            kind="schedule",
        )

    with pytest.raises(ValueError, match="schedule_timezone is required"):
        repo.create(
            owner_user_id=uid,
            scope_path="a.md",
            nl_description="something",
            message="hi",
            kind="schedule",
            schedule_cron="*/15 * * * *",
        )


def test_create_schedule_rejects_bad_cron(tmp_repo):
    from app.triggers import repo

    uid = seed_user(email="a@b.com")
    with pytest.raises(ValueError, match="not a valid 5-field cron"):
        repo.create(
            owner_user_id=uid,
            scope_path="a.md",
            nl_description="x",
            message="m",
            kind="schedule",
            schedule_cron="not a cron",
            schedule_timezone="UTC",
        )


def test_create_schedule_rejects_bad_tz(tmp_repo):
    from app.triggers import repo

    uid = seed_user(email="a@b.com")
    with pytest.raises(ValueError, match="not a known IANA name"):
        repo.create(
            owner_user_id=uid,
            scope_path="a.md",
            nl_description="x",
            message="m",
            kind="schedule",
            schedule_cron="*/15 * * * *",
            schedule_timezone="Mars/Olympus_Mons",
        )


def test_create_schedule_rejects_bad_start_at(tmp_repo):
    from app.triggers import repo

    uid = seed_user(email="a@b.com")
    with pytest.raises(ValueError, match="ISO 8601"):
        repo.create(
            owner_user_id=uid,
            scope_path="a.md",
            nl_description="x",
            message="m",
            kind="schedule",
            schedule_cron="*/15 * * * *",
            schedule_timezone="UTC",
            schedule_start_at="not-a-timestamp",
        )


def test_delta_rejects_schedule_fields(tmp_repo):
    from app.triggers import repo

    uid = seed_user(email="a@b.com")
    with pytest.raises(ValueError, match="must be null for delta"):
        repo.create(
            owner_user_id=uid,
            scope_path="a.md",
            nl_description="x",
            message="m",
            kind="delta",
            schedule_cron="*/15 * * * *",
        )


def test_create_schedule_persists_round_trip(tmp_repo):
    from app.triggers import repo

    uid = seed_user(email="a@b.com")
    t = repo.create(
        owner_user_id=uid,
        scope_path="projects/foo.md",
        nl_description="status changed",
        message="hi",
        kind="schedule",
        schedule_cron="0 9 * * 1",
        schedule_timezone="America/Los_Angeles",
        schedule_start_at="2026-06-01T00:00:00+00:00",
    )
    assert t["kind"] == "schedule"
    assert t["schedule_cron"] == "0 9 * * 1"
    assert t["schedule_timezone"] == "America/Los_Angeles"
    assert t["schedule_start_at"] == "2026-06-01T00:00:00+00:00"
    assert t["schedule_last_fired_at"] is None

    fetched = repo.get(t["id"])
    assert fetched is not None
    assert fetched == t


# --------------------------------------------------------------------------- #
# find_due_schedule_triggers                                                  #
# --------------------------------------------------------------------------- #


def test_find_due_excludes_delta(tmp_db):
    from app.triggers.engine import find_due_schedule_triggers

    uid = seed_user(is_admin=True)
    last = _iso(_now() - timedelta(minutes=10))
    seed_trigger(tid="t_delta", owner_user_id=uid, scope_path="a.md")
    seed_trigger(
        tid="t_sched",
        owner_user_id=uid,
        scope_path="a.md",
        kind="schedule",
        schedule_cron="* * * * *",
        schedule_timezone="UTC",
        schedule_last_fired_at=last,
    )
    rows = find_due_schedule_triggers(_now())
    assert {r.id for r in rows} == {"t_sched"}


def test_find_due_excludes_disabled(tmp_db):
    from app.triggers.engine import find_due_schedule_triggers

    uid = seed_user(is_admin=True)
    seed_trigger(
        tid="t_off",
        owner_user_id=uid,
        scope_path="a.md",
        kind="schedule",
        enabled=False,
        schedule_cron="* * * * *",
        schedule_timezone="UTC",
    )
    assert find_due_schedule_triggers(_now()) == []


def test_find_due_skips_before_start_at(tmp_db):
    from app.triggers.engine import find_due_schedule_triggers

    uid = seed_user(is_admin=True)
    future = _iso(_now() + timedelta(hours=1))
    seed_trigger(
        tid="t_future",
        owner_user_id=uid,
        scope_path="a.md",
        kind="schedule",
        schedule_cron="* * * * *",
        schedule_timezone="UTC",
        schedule_start_at=future,
    )
    assert find_due_schedule_triggers(_now()) == []


def test_find_due_includes_due_after_last_fire(tmp_db):
    from app.triggers.engine import find_due_schedule_triggers

    uid = seed_user(is_admin=True)
    last = _iso(_now() - timedelta(minutes=10))
    seed_trigger(
        tid="t_due",
        owner_user_id=uid,
        scope_path="a.md",
        kind="schedule",
        schedule_cron="* * * * *",
        schedule_timezone="UTC",
        schedule_last_fired_at=last,
    )
    rows = find_due_schedule_triggers(_now())
    assert {r.id for r in rows} == {"t_due"}


def test_find_due_skips_when_just_fired(tmp_db):
    from app.triggers.engine import find_due_schedule_triggers

    uid = seed_user(is_admin=True)
    # Cron fires every hour on the hour; last fire was right now, so the
    # next due time is the next top-of-hour, which is always in the future.
    # Using now-30s would fail if the test runs in the first 30s of any hour
    # (the boundary at HH:00:00 would already be <= now).
    last = _iso(_now())
    seed_trigger(
        tid="t_recent",
        owner_user_id=uid,
        scope_path="a.md",
        kind="schedule",
        schedule_cron="0 * * * *",
        schedule_timezone="UTC",
        schedule_last_fired_at=last,
    )
    assert find_due_schedule_triggers(_now()) == []


# --------------------------------------------------------------------------- #
# evaluate_due_schedule_triggers — the periodic body                          #
# --------------------------------------------------------------------------- #


def test_evaluator_records_fire_on_match(tmp_repo):
    from app.tasks.triggers import evaluate_due_schedule_triggers
    from app.triggers import repo

    uid = seed_user(is_admin=True, email="a@b.com")
    last = _iso(_now() - timedelta(minutes=10))
    t = seed_trigger(
        tid="trg_sched_1",
        owner_user_id=uid,
        scope_path="",
        kind="schedule",
        nl_description="any change at all",
        message="ping",
        schedule_cron="* * * * *",
        schedule_timezone="UTC",
        schedule_last_fired_at=last,
    )

    responses = [_matched_response(True, "test"), _rendered_response("hello")]
    with patch(
        "app.triggers.natural_language.complete",
        side_effect=lambda *a, **kw: responses.pop(0),
    ):
        fired = evaluate_due_schedule_triggers(_now())

    assert fired == 1
    fires = list_events(kind="trigger.fire")
    assert len(fires) == 1
    payload = fires[0]["payload"]
    assert payload["trigger_id"] == t
    assert payload["change_kind"] == "schedule"
    assert payload["message"] == "hello"

    # last_fired_at advanced
    fetched = repo.get(t)
    assert fetched is not None
    assert fetched["schedule_last_fired_at"] is not None
    assert fetched["schedule_last_fired_at"] > last


def test_evaluator_no_match_still_advances_last_fired(tmp_repo):
    from app.tasks.triggers import evaluate_due_schedule_triggers
    from app.triggers import repo

    uid = seed_user(is_admin=True, email="a@b.com")
    last = _iso(_now() - timedelta(minutes=10))
    t = seed_trigger(
        tid="trg_sched_nm",
        owner_user_id=uid,
        scope_path="",
        kind="schedule",
        nl_description="never",
        message="ping",
        schedule_cron="* * * * *",
        schedule_timezone="UTC",
        schedule_last_fired_at=last,
    )

    with patch(
        "app.triggers.natural_language.complete",
        side_effect=lambda *a, **kw: _matched_response(False, "no"),
    ):
        fired = evaluate_due_schedule_triggers(_now())

    assert fired == 0
    assert list_events(kind="trigger.fire") == []
    fetched = repo.get(t)
    assert fetched is not None
    assert fetched["schedule_last_fired_at"] is not None
    # Advances past the seeded last (10 min ago), even on no-match.
    assert fetched["schedule_last_fired_at"] > last


def test_evaluator_skips_owner_without_read(tmp_repo):
    """ACL re-check: owner whose read access was revoked doesn't fire."""
    from app.tasks.triggers import evaluate_due_schedule_triggers
    from app.wiki import acl as wiki_acl

    # Trigger owner is non-admin; doc is owned by someone else, so the
    # owner has no read access (private-by-owner default).
    owner = seed_user(uid="usr_owner", is_admin=False, email="o@x.com")
    other = seed_user(uid="usr_other", email="x@x.com")
    wiki_acl.set_owner("restricted.md", other)

    last = _iso(_now() - timedelta(minutes=10))
    seed_trigger(
        tid="trg_sched_acl",
        owner_user_id=owner,
        scope_path="restricted.md",
        kind="schedule",
        nl_description="anything",
        message="ping",
        schedule_cron="* * * * *",
        schedule_timezone="UTC",
        schedule_last_fired_at=last,
    )

    with patch(
        "app.triggers.natural_language.complete",
        side_effect=AssertionError("LLM should not be called when ACL fails"),
    ):
        fired = evaluate_due_schedule_triggers(_now())

    assert fired == 0
    assert list_events(kind="trigger.fire") == []


# --------------------------------------------------------------------------- #
# schedule_window_start — the "changes since last check" window               #
# --------------------------------------------------------------------------- #


def test_window_start_uses_last_fired_when_present():
    from app.triggers.engine import schedule_window_start

    now = _now()
    last = now - timedelta(minutes=10)
    start = schedule_window_start(_record(schedule_last_fired_at=_iso(last)), now)
    assert abs((start - last).total_seconds()) < 1


def test_window_start_first_fire_uses_previous_cron_tick():
    from app.triggers.engine import schedule_window_start

    now = _now()
    # Hourly cron, never fired: window start is the most recent top-of-hour
    # at or before now — i.e. < 1h back, on the hour boundary.
    start = schedule_window_start(_record(schedule_last_fired_at=None), now)
    assert start <= now
    assert now - start < timedelta(hours=1)
    assert start.minute == 0 and start.second == 0


def test_window_start_clamps_stale_base():
    from app.triggers.engine import schedule_window_start

    now = _now()
    stale = now - timedelta(days=30)
    start = schedule_window_start(_record(schedule_last_fired_at=_iso(stale)), now)
    # Clamped to the 14-day lookback floor, not the 30-day-old base.
    assert abs((now - start) - timedelta(days=14)).total_seconds() < 60


# --------------------------------------------------------------------------- #
# evaluator threads the changes-since diff into the payload                   #
# --------------------------------------------------------------------------- #


def test_evaluator_payload_includes_changes_since_block(tmp_repo):
    from app.tasks.triggers import evaluate_due_schedule_triggers
    from app.wiki import git as wiki_git

    uid = seed_user(is_admin=True, email="a@b.com")
    wiki_git.commit_file("watched.md", "# Watched\n\nstatus: green\n", "seed", author=None)
    last = _iso(_now() - timedelta(minutes=10))
    seed_trigger(
        tid="trg_sched_diff",
        owner_user_id=uid,
        scope_path="",
        kind="schedule",
        nl_description="any change",
        message="ping",
        schedule_cron="* * * * *",
        schedule_timezone="UTC",
        schedule_last_fired_at=last,
    )

    seen: list[str] = []

    def _capture(messages, **kw):
        seen.append(messages[1]["content"])
        # First call is the eval gate, second is render.
        return _matched_response(True, "changed") if len(seen) == 1 else _rendered_response("ok")

    with patch("app.triggers.natural_language.complete", side_effect=_capture):
        evaluate_due_schedule_triggers(_now())

    assert seen, "evaluator never called the LLM"
    assert "CHANGES SINCE LAST CHECK" in seen[0]
    # watched.md was committed within the 10-min window → shows as a change.
    assert "watched.md" in seen[0]


# --------------------------------------------------------------------------- #
# storage / rebuild round-trip                                                #
# --------------------------------------------------------------------------- #


def test_rebuild_from_filesystem_loads_schedule_yaml(tmp_repo):
    from app.triggers import repo, storage

    uid = seed_user(email="a@b.com")
    created = repo.create(
        owner_user_id=uid,
        scope_path="watched.md",
        nl_description="status",
        message="msg",
        kind="schedule",
        schedule_cron="0 9 * * 1",
        schedule_timezone="UTC",
    )
    # YAML round-trip
    raw = storage.read_trigger(created["file_path"])
    assert raw["schedule_cron"] == "0 9 * * 1"
    assert raw["schedule_timezone"] == "UTC"
    assert raw["kind"] == "schedule"

    # Wipe DB cache; rebuild from on-disk YAML; assert columns hydrated.
    from app.db.models import Trigger
    from app.db.session import session
    from sqlalchemy import delete as sa_delete

    with session() as s:
        s.execute(sa_delete(Trigger))

    loaded = repo.rebuild_from_filesystem()
    assert loaded == 1
    fetched = repo.get(created["id"])
    assert fetched is not None
    assert fetched["kind"] == "schedule"
    assert fetched["schedule_cron"] == "0 9 * * 1"
    assert fetched["schedule_timezone"] == "UTC"
