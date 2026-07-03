"""Tests for the trigger tools (create_trigger, update_trigger).

Direct handler tests against a tmp wiki repo + DB. We stub
``current_user`` since the handlers depend on it for ownership.
"""
from __future__ import annotations

import pytest

from tests._seed import seed_docs, seed_user


@pytest.fixture
def as_user(tmp_repo, monkeypatch):
    """Seed a user and stub current_user so the tool handlers see them."""
    seed_docs("a.md", "b.md", "guide.md", "public.md", "private/secret.md")
    uid = seed_user(email="u@x.com")

    class FakeUser:
        id = uid
        email = "u@x.com"
        name = "U"
        is_admin = False

    monkeypatch.setattr(
        "app.llm.agents.tools.create_trigger.current_user", lambda: FakeUser()
    )
    monkeypatch.setattr(
        "app.llm.agents.tools.get_destination_configs.current_user", lambda: FakeUser()
    )
    monkeypatch.setattr(
        "app.llm.agents.tools.update_trigger.current_user", lambda: FakeUser()
    )
    return uid


# --------------------------------------------------------------------------- #
# create_trigger                                                              #
# --------------------------------------------------------------------------- #


def test_create_trigger_warns_on_not_yet_created_scope(as_user):
    from app.llm.agents.tools.create_trigger import handle

    out = handle(
        {
            "scope_path": "roadmap/q3.md",
            "trigger_nl_condition": "always",
            "actions": [{"message": "hi"}],
        }
    )
    assert "error" not in out, out
    assert "does not exist yet" in out["scope_warning"]


def test_create_trigger_requires_actions(as_user):
    from app.llm.agents.tools.create_trigger import handle

    out = handle(
        {"scope_path": "guide.md", "trigger_nl_condition": "fire on rewrite"}
    )
    assert "error" in out
    assert "actions" in out["error"]


def test_create_trigger_defaults_to_event_log(as_user):
    from app.llm.agents.tools.create_trigger import handle

    out = handle(
        {
            "scope_path": "guide.md",
            "trigger_nl_condition": "fire on rewrite",
            "actions": [{"message": "Guide rewritten"}],
        }
    )
    assert "error" not in out, out
    t = out["trigger"]
    assert t["actions"] == [{"destination_config_id": None, "message": "Guide rewritten"}]


# --------------------------------------------------------------------------- #
# update_trigger                                                              #
# --------------------------------------------------------------------------- #


def _seed_trigger(uid: str, **kw) -> str:
    from typing import Any
    from app.triggers import repo

    args: dict[str, Any] = dict(
        owner_user_id=uid,
        scope_path="guide.md",
        nl_description="orig",
        actions=[{"message": "orig msg"}],
    )
    args.update(kw)
    return repo.create(**args)["id"]


def test_update_trigger_changes_individual_fields(as_user):
    from app.llm.agents.tools.update_trigger import handle

    tid = _seed_trigger(as_user)

    out = handle({"trigger_id": tid, "actions": [{"message": "new msg"}]})
    assert "error" not in out, out
    assert out["trigger"]["actions"][0]["message"] == "new msg"
    assert out["trigger"]["nl_description"] == "orig"

    out = handle({"trigger_id": tid, "trigger_nl_condition": "new cond"})
    assert out["trigger"]["nl_description"] == "new cond"
    assert out["trigger"]["actions"][0]["message"] == "new msg"

    out = handle({"trigger_id": tid, "enabled": False})
    assert out["trigger"]["enabled"] is False


def test_update_trigger_rejects_other_users_trigger(as_user, monkeypatch):
    """Even if you know a trigger_id, you can't modify someone else's."""
    from app.llm.agents.tools.update_trigger import handle

    # Seed a second user and a trigger they own.
    other_id = seed_user(uid="usr_2", email="b@x.com")
    other_trigger = _seed_trigger(other_id)

    out = handle({"trigger_id": other_trigger, "actions": [{"message": "hijack"}]})
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


# --------------------------------------------------------------------------- #
# scope_path ACL gating                                                        #
# --------------------------------------------------------------------------- #


def test_create_trigger_blocks_unreadable_scope(as_user):
    from app.llm.agents.tools.create_trigger import handle
    from app.wiki import acl

    other_owner = seed_user(uid="usr_owner", email="o@x.com")
    acl.set_owner("private/secret.md", other_owner)

    out = handle(
        {
            "scope_path": "private/secret.md",
            "trigger_nl_condition": "fire",
            "actions": [{"message": "msg"}],
        }
    )
    assert "error" in out
    assert "read access" in out["error"]


def test_update_trigger_blocks_rebinding_to_unreadable_scope(as_user):
    from app.llm.agents.tools.update_trigger import handle
    from app.wiki import acl

    tid = _seed_trigger(as_user)
    other_owner = seed_user(uid="usr_owner", email="o@x.com")
    acl.set_owner("private/secret.md", other_owner)

    out = handle({"trigger_id": tid, "scope_path": "private/secret.md"})
    assert "error" in out
    assert "read access" in out["error"]


def test_create_trigger_allowed_with_explicit_grant(as_user):
    """Positive regression: a managed-path scope works when the user has
    an explicit `read` grant. Pairs with the unreadable-scope test above to
    pin both directions of the gate."""
    from app.llm.agents.tools.create_trigger import handle
    from app.wiki import acl

    other_owner = seed_user(uid="usr_owner", email="o@x.com")
    acl.set_owner("private/secret.md", other_owner)
    acl.grant(
        resource_kind="page",
        resource_path="private/secret.md",
        principal_kind="user",
        principal_id=as_user,
        permission="read",
        granted_by_user_id=other_owner,
    )

    out = handle(
        {
            "scope_path": "private/secret.md",
            "trigger_nl_condition": "fire",
            "actions": [{"message": "msg"}],
        }
    )
    assert "error" not in out, out
    assert out["trigger"]["scope_path"] == "private/secret.md"


def test_update_trigger_blocks_when_existing_scope_unreadable(as_user):
    """Negative regression: a user who owns a trigger but lost read access
    to its *existing* scope can't even toggle ``enabled``."""
    from app.llm.agents.tools.update_trigger import handle
    from app.wiki import acl

    other_owner = seed_user(uid="usr_owner", email="o@x.com")
    acl.set_owner("private/secret.md", other_owner)
    grant_id = acl.grant(
        resource_kind="page",
        resource_path="private/secret.md",
        principal_kind="user",
        principal_id=as_user,
        permission="read",
        granted_by_user_id=other_owner,
    )

    tid = _seed_trigger(as_user, scope_path="private/secret.md")
    acl.revoke(grant_id)

    out = handle({"trigger_id": tid, "enabled": False})
    assert "error" in out
    assert "read access" in out["error"]


def test_create_trigger_multi_action_via_tool(as_user):
    from app.llm.agents.tools.create_trigger import handle
    from app.triggers import destination_configs as dest_configs

    cfg = dest_configs.create(
        as_user, type="slack", name="PM", secret="https://hooks.slack.com/x"
    )
    out = handle(
        {
            "scope_path": "guide.md",
            "trigger_nl_condition": "fire on rewrite",
            "actions": [
                {"message": "to the log"},
                {"message": "to slack", "destination_config_id": cfg["id"]},
            ],
        }
    )
    assert "error" not in out, out
    assert out["trigger"]["actions"] == [
        {"destination_config_id": None, "message": "to the log"},
        {"destination_config_id": cfg["id"], "message": "to slack"},
    ]


def test_get_destination_configs_lists_own_only(as_user):
    from app.llm.agents.tools.get_destination_configs import handle
    from app.triggers import destination_configs as dest_configs

    from tests._seed import seed_user

    dest_configs.create(as_user, type="slack", name="Mine", secret="https://hooks.slack.com/a")
    other = seed_user(uid="usr_other", email="other@x.com")
    dest_configs.create(other, type="slack", name="Theirs", secret="https://hooks.slack.com/b")

    out = handle({})
    names = [c["name"] for c in out["destination_configs"]]
    assert names == ["Mine"]


def test_update_trigger_rejects_non_list_actions(as_user):
    from app.llm.agents.tools.update_trigger import handle

    tid = _seed_trigger(as_user)
    out = handle({"trigger_id": tid, "actions": "not a list"})
    assert "error" in out
    assert "array" in out["error"]
