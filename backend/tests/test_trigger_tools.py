"""Tests for the trigger tools (create_trigger, update_trigger).

Direct handler tests against a tmp wiki repo + sqlite. We stub
``current_user`` since the handlers depend on it for ownership.
"""
from __future__ import annotations

import pytest

from app.db.sqlite import connect


def _seed_user(uid: str = "usr_1", email: str = "u@x.com") -> str:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO users(id, email, password_hash, is_admin) VALUES (?, ?, ?, 0)",
            (uid, email, "x"),
        )
    finally:
        conn.close()
    return uid


@pytest.fixture
def as_user(tmp_repo, monkeypatch):
    """Seed a user and stub current_user so the tool handlers see them."""
    uid = _seed_user()

    class FakeUser:
        id = uid
        email = "u@x.com"
        name = "U"

    monkeypatch.setattr(
        "app.llm.agents.tools.create_trigger.current_user", lambda: FakeUser()
    )
    monkeypatch.setattr(
        "app.llm.agents.tools.update_trigger.current_user", lambda: FakeUser()
    )
    return uid


# --------------------------------------------------------------------------- #
# create_trigger                                                              #
# --------------------------------------------------------------------------- #


def test_create_trigger_requires_message(as_user):
    from app.llm.agents.tools.create_trigger import handle

    out = handle(
        {"scope_path": "guide.md", "trigger_nl_condition": "fire on rewrite"}
    )
    assert "error" in out
    assert "trigger_fire_message" in out["error"]


def test_create_trigger_default_destination_is_null(as_user):
    from app.llm.agents.tools.create_trigger import handle

    out = handle(
        {
            "scope_path": "guide.md",
            "trigger_nl_condition": "fire on rewrite",
            "trigger_fire_message": "Guide rewritten",
        }
    )
    assert "error" not in out, out
    t = out["trigger"]
    assert t["message"] == "Guide rewritten"
    assert t["destination"] is None


def test_create_trigger_rejects_non_null_destination(as_user):
    from app.llm.agents.tools.create_trigger import handle

    out = handle(
        {
            "scope_path": "guide.md",
            "trigger_nl_condition": "fire on rewrite",
            "trigger_fire_message": "x",
            "destination": "https://example.com/hook",
        }
    )
    assert "error" in out
    assert "destination" in out["error"]


# --------------------------------------------------------------------------- #
# update_trigger                                                              #
# --------------------------------------------------------------------------- #


def _seed_trigger(uid: str, **kw) -> str:
    from app.triggers import repo

    args = dict(
        owner_user_id=uid,
        scope_path="guide.md",
        nl_description="orig",
        message="orig msg",
    )
    args.update(kw)
    return repo.create(**args)["id"]


def test_update_trigger_changes_individual_fields(as_user):
    from app.llm.agents.tools.update_trigger import handle

    tid = _seed_trigger(as_user)

    out = handle({"trigger_id": tid, "trigger_fire_message": "new msg"})
    assert "error" not in out, out
    assert out["trigger"]["message"] == "new msg"
    assert out["trigger"]["nl_description"] == "orig"

    out = handle({"trigger_id": tid, "trigger_nl_condition": "new cond"})
    assert out["trigger"]["nl_description"] == "new cond"
    assert out["trigger"]["message"] == "new msg"

    out = handle({"trigger_id": tid, "enabled": False})
    assert out["trigger"]["enabled"] is False


def test_update_trigger_rejects_non_null_destination(as_user):
    from app.llm.agents.tools.update_trigger import handle

    tid = _seed_trigger(as_user)
    out = handle({"trigger_id": tid, "destination": "slack://channel"})
    assert "error" in out
    assert "destination" in out["error"]


def test_update_trigger_accepts_explicit_null_destination(as_user):
    from app.llm.agents.tools.update_trigger import handle

    tid = _seed_trigger(as_user)
    out = handle({"trigger_id": tid, "destination": None})
    assert "error" not in out, out
    assert out["trigger"]["destination"] is None


def test_update_trigger_rejects_other_users_trigger(as_user, monkeypatch):
    """Even if you know a trigger_id, you can't modify someone else's."""
    from app.llm.agents.tools.update_trigger import handle

    # Seed a second user and a trigger they own.
    other_id = _seed_user(uid="usr_2", email="b@x.com")
    other_trigger = _seed_trigger(other_id)

    out = handle({"trigger_id": other_trigger, "trigger_fire_message": "hijack"})
    assert "error" in out
    assert "do not own" in out["error"]


def test_update_trigger_404_for_unknown_id(as_user):
    from app.llm.agents.tools.update_trigger import handle

    out = handle({"trigger_id": "trg_doesnotexist"})
    assert "error" in out
    assert "not found" in out["error"]


def test_update_trigger_no_fields_returns_current(as_user):
    from app.llm.agents.tools.update_trigger import handle

    tid = _seed_trigger(as_user)
    out = handle({"trigger_id": tid})
    assert "error" not in out, out
    assert out["trigger"]["id"] == tid
    assert out.get("note") == "no fields to update"
