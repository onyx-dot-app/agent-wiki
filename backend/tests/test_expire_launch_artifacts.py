"""Periodic sweep — launch_codes + stale sessions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.auth import launch_codes as codes_repo
from app.auth import mcp_tokens as tokens_repo
from app.db.models import AgentSession
from app.db.session import init_db, session
from app.launchers import sessions as sessions_repo
from app.tasks.expire_launch_artifacts import expire_launch_artifacts
from app.tasks.queues import lightweight_maintenance_queue

from tests._seed import seed_user


def _get_session_dict(sid):
    from app.launchers import sessions as _sr
    row = _sr.get(sid)
    assert row is not None
    return row


def test_sweep_runs_on_empty_db(tmp_config):
    init_db()
    with lightweight_maintenance_queue.immediate_mode():
        expire_launch_artifacts()  # no-op, no error


def test_sweep_deletes_expired_codes(tmp_config, monkeypatch):
    init_db()
    uid = seed_user()
    sid = sessions_repo.create(
        user_id=uid,
        tool_id="claude-code",
        first_turn_prompt="x",
        wiki_path=None,
        working_dir=None,
    )
    tid, _ = tokens_repo.create(uid, "k")
    monkeypatch.setattr(
        codes_repo,
        "CONFIG",
        codes_repo.CONFIG.model_copy(update={"launch_code_ttl_seconds": 0}),
    )
    codes_repo.create(user_id=uid, agent_session_id=sid, mcp_token_id=tid)

    with lightweight_maintenance_queue.immediate_mode():
        expire_launch_artifacts()
    assert codes_repo.expire_sweep() == 0


def test_sweep_marks_stale_active_as_idle(tmp_config, monkeypatch):
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
    monkeypatch.setattr(
        sessions_repo,
        "CONFIG",
        sessions_repo.CONFIG.model_copy(update={"agent_session_idle_seconds": 0}),
    )
    with lightweight_maintenance_queue.immediate_mode():
        expire_launch_artifacts()
    assert _get_session_dict(sid)["status"] == "idle"


def test_sweep_marks_failed_when_no_spawn_ok_within_30s(tmp_config):
    """R9#1 — active session without spawn beacon → failed."""
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
    # Backdate started_at to 60s ago.
    past = (datetime.now(timezone.utc) - timedelta(seconds=60)).strftime("%Y-%m-%d %H:%M:%S")
    with session() as s:
        s.execute(update(AgentSession).where(AgentSession.id == sid).values(started_at=past))
    with lightweight_maintenance_queue.immediate_mode():
        expire_launch_artifacts()
    assert _get_session_dict(sid)["status"] == "failed"
