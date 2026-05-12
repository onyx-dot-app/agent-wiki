"""Repo round-trip for short-lived launch codes."""

from __future__ import annotations

from app.auth import launch_codes as codes_repo
from app.auth import mcp_tokens as tokens_repo
from app.db.models import AgentSession
from app.db.session import init_db, session

from tests._seed import seed_user


def _seed_session(uid: str, sid: str = "as_1") -> str:
    with session() as s:
        s.add(
            AgentSession(
                id=sid,
                user_id=uid,
                tool_id="claude-code",
                first_turn_prompt="hello",
            )
        )
    return sid


def test_create_roundtrip(tmp_config):
    init_db()
    uid = seed_user()
    sid = _seed_session(uid)
    tid, _ = tokens_repo.create(uid, "k")

    raw = codes_repo.create(user_id=uid, agent_session_id=sid, mcp_token_id=tid)

    assert raw.startswith("lc_")
    consumed = codes_repo.consume(raw)
    assert isinstance(consumed, dict)
    assert consumed["user_id"] == uid
    assert consumed["agent_session_id"] == sid
    assert consumed["mcp_token_id"] == tid


def test_consume_idempotent_second_call_returns_consumed_marker(tmp_config):
    init_db()
    uid = seed_user()
    sid = _seed_session(uid)
    tid, _ = tokens_repo.create(uid, "k")
    raw = codes_repo.create(user_id=uid, agent_session_id=sid, mcp_token_id=tid)
    assert isinstance(codes_repo.consume(raw), dict)
    assert codes_repo.consume(raw) == "already_consumed"


def test_consume_expired_returns_expired(tmp_config, monkeypatch):
    """TTL=0 → any code is immediately expired."""
    init_db()
    uid = seed_user()
    sid = _seed_session(uid)
    tid, _ = tokens_repo.create(uid, "k")
    # Patch CONFIG.launch_code_ttl_seconds via monkeypatch on the frozen
    # pydantic model — use the imported reference in codes_repo.
    monkeypatch.setattr(
        codes_repo,
        "CONFIG",
        codes_repo.CONFIG.model_copy(update={"launch_code_ttl_seconds": 0}),
    )
    raw = codes_repo.create(user_id=uid, agent_session_id=sid, mcp_token_id=tid)
    assert codes_repo.consume(raw) == "expired"


def test_consume_unknown_returns_none(tmp_config):
    init_db()
    assert codes_repo.consume("lc_does_not_exist") is None


def test_consume_malformed_prefix_returns_none(tmp_config):
    init_db()
    assert codes_repo.consume("xx_not_a_code") is None


def test_expire_sweep_deletes_old_codes(tmp_config, monkeypatch):
    init_db()
    uid = seed_user()
    sid = _seed_session(uid)
    tid, _ = tokens_repo.create(uid, "k")
    monkeypatch.setattr(
        codes_repo,
        "CONFIG",
        codes_repo.CONFIG.model_copy(update={"launch_code_ttl_seconds": 0}),
    )
    codes_repo.create(user_id=uid, agent_session_id=sid, mcp_token_id=tid)
    assert codes_repo.expire_sweep() == 1
    assert codes_repo.expire_sweep() == 0
