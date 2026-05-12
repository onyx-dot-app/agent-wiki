"""Repo round-trip for agent_sessions."""

from __future__ import annotations

from app.config import Config
from app.db.session import init_db
from app.launchers import sessions as sessions_repo

from tests._seed import seed_user


def _shrink_idle(monkeypatch, *, idle=0, close_after=0):
    """Replace ``CONFIG`` in the sessions module with a copy that has
    near-zero idle/close thresholds so sweeps act immediately."""
    monkeypatch.setattr(
        sessions_repo,
        "CONFIG",
        sessions_repo.CONFIG.model_copy(
            update={
                "agent_session_idle_seconds": idle,
                "agent_session_close_after_idle_seconds": close_after,
            }
        ),
    )


def test_create_minimal(tmp_config):
    init_db()
    uid = seed_user()
    sid = sessions_repo.create(
        user_id=uid,
        tool_id="claude-code",
        first_turn_prompt="hello",
        wiki_path="docs/x.md",
        working_dir="/tmp/work",
    )
    assert sid.startswith("as_")
    row = sessions_repo.get(sid)
    assert row is not None
    assert row["user_id"] == uid
    assert row["status"] == "pending"
    assert row["machine_id"] is None


def test_mark_active_sets_machine_id(tmp_config):
    init_db()
    uid = seed_user()
    sid = sessions_repo.create(
        user_id=uid,
        tool_id="claude-code",
        first_turn_prompt="x",
        wiki_path=None,
        working_dir=None,
    )
    sessions_repo.mark_active(sid, machine_id="m_abc")
    row = sessions_repo.get(sid)
    assert row["status"] == "active"
    assert row["machine_id"] == "m_abc"


def test_set_cli_session_id(tmp_config):
    init_db()
    uid = seed_user()
    sid = sessions_repo.create(
        user_id=uid,
        tool_id="claude-code",
        first_turn_prompt="x",
        wiki_path=None,
        working_dir=None,
    )
    sessions_repo.set_cli_session_id(sid, "cli_xyz")
    assert sessions_repo.get(sid)["cli_session_id"] == "cli_xyz"


def test_mark_spawn_ok(tmp_config):
    init_db()
    uid = seed_user()
    sid = sessions_repo.create(
        user_id=uid,
        tool_id="claude-code",
        first_turn_prompt="x",
        wiki_path=None,
        working_dir=None,
    )
    assert sessions_repo.get(sid)["spawn_ok_at"] is None
    sessions_repo.mark_spawn_ok(sid)
    assert sessions_repo.get(sid)["spawn_ok_at"] is not None


def test_touch_activity_does_not_resurrect_closed(tmp_config):
    """R5#2 — closed/failed sessions stay closed even if a stale helper
    keeps heartbeating."""
    init_db()
    uid = seed_user()
    sid = sessions_repo.create(
        user_id=uid,
        tool_id="claude-code",
        first_turn_prompt="x",
        wiki_path=None,
        working_dir=None,
    )
    sessions_repo.close(sid, reason="user")
    before = sessions_repo.get(sid)
    sessions_repo.touch_activity(sid)
    after = sessions_repo.get(sid)
    assert after["status"] == "closed"
    assert after["last_activity_at"] == before["last_activity_at"]


def test_close_marks_status_and_closed_at(tmp_config):
    init_db()
    uid = seed_user()
    sid = sessions_repo.create(
        user_id=uid,
        tool_id="claude-code",
        first_turn_prompt="x",
        wiki_path=None,
        working_dir=None,
    )
    sessions_repo.close(sid, reason="user_clicked")
    row = sessions_repo.get(sid)
    assert row["status"] == "closed"
    assert row["closed_at"] is not None


def test_mark_failed(tmp_config):
    init_db()
    uid = seed_user()
    sid = sessions_repo.create(
        user_id=uid,
        tool_id="claude-code",
        first_turn_prompt="x",
        wiki_path=None,
        working_dir=None,
    )
    sessions_repo.mark_failed(sid, reason="cli_not_found")
    row = sessions_repo.get(sid)
    assert row["status"] == "failed"
    assert row["closed_at"] is not None


def test_list_for_user_excludes_closed_by_default(tmp_config):
    init_db()
    uid = seed_user()
    a = sessions_repo.create(
        user_id=uid,
        tool_id="claude-code",
        first_turn_prompt="x",
        wiki_path="p1.md",
        working_dir=None,
    )
    b = sessions_repo.create(
        user_id=uid,
        tool_id="claude-code",
        first_turn_prompt="x",
        wiki_path="p2.md",
        working_dir=None,
    )
    sessions_repo.close(a, reason="user")
    open_rows = sessions_repo.list_for_user(uid)
    assert {r["id"] for r in open_rows} == {b}


def test_list_for_user_strips_first_turn_prompt(tmp_config):
    """R2#5 — list responses don't include first_turn_prompt."""
    init_db()
    uid = seed_user()
    sessions_repo.create(
        user_id=uid,
        tool_id="claude-code",
        first_turn_prompt="SENSITIVE",
        wiki_path="p1.md",
        working_dir=None,
    )
    rows = sessions_repo.list_for_user(uid)
    assert "first_turn_prompt" not in rows[0]


def test_list_for_page(tmp_config):
    init_db()
    uid = seed_user()
    a = sessions_repo.create(
        user_id=uid,
        tool_id="claude-code",
        first_turn_prompt="x",
        wiki_path="match.md",
        working_dir=None,
    )
    sessions_repo.create(
        user_id=uid,
        tool_id="claude-code",
        first_turn_prompt="x",
        wiki_path="other.md",
        working_dir=None,
    )
    rows = sessions_repo.list_for_page(user_id=uid, wiki_path="match.md")
    assert {r["id"] for r in rows} == {a}


def test_sweep_marks_idle(tmp_config, monkeypatch):
    init_db()
    uid = seed_user()
    sid = sessions_repo.create(
        user_id=uid,
        tool_id="claude-code",
        first_turn_prompt="x",
        wiki_path=None,
        working_dir=None,
    )
    sessions_repo.mark_active(sid, machine_id="m")
    _shrink_idle(monkeypatch, idle=0)
    n = sessions_repo.mark_stale_idle()
    assert n == 1
    assert sessions_repo.get(sid)["status"] == "idle"


def test_sweep_evicts_idle_to_closed_across_two_ticks(tmp_config, monkeypatch):
    """R5#3 — idle → closed cross-tick."""
    init_db()
    uid = seed_user()
    sid = sessions_repo.create(
        user_id=uid,
        tool_id="claude-code",
        first_turn_prompt="x",
        wiki_path=None,
        working_dir=None,
    )
    sessions_repo.mark_active(sid, machine_id="m")
    _shrink_idle(monkeypatch, idle=0, close_after=0)
    sessions_repo.mark_stale_idle()
    n = sessions_repo.evict_idle_to_closed()
    assert n == 1
    assert sessions_repo.get(sid)["status"] == "closed"


def test_evict_spawn_missed(tmp_config, monkeypatch):
    """R9#1 — active session without spawn_ok beacon gets failed after 30s."""
    import time
    from datetime import datetime, timedelta, timezone

    init_db()
    uid = seed_user()
    sid = sessions_repo.create(
        user_id=uid,
        tool_id="claude-code",
        first_turn_prompt="x",
        wiki_path=None,
        working_dir=None,
    )
    sessions_repo.mark_active(sid, machine_id="m")
    # Backdate started_at to 60s ago so sweep matches.
    from sqlalchemy import update as _u
    from app.db.models import AgentSession as _AS
    from app.db.session import session as _ss

    past = (datetime.now(timezone.utc) - timedelta(seconds=60)).strftime("%Y-%m-%d %H:%M:%S")
    with _ss() as s:
        s.execute(_u(_AS).where(_AS.id == sid).values(started_at=past))
    n = sessions_repo.evict_spawn_missed()
    assert n == 1
    assert sessions_repo.get(sid)["status"] == "failed"
