"""Unit tests for ``app/wiki/agent_activity.py``.

The module is a thin DB layer on top of the ``agent_activity`` table.
Tests pull in ``tmp_db`` plus a seeded ``User`` row so the join in
``_select_with_owner`` has something to attach to.
"""
from __future__ import annotations

from datetime import timedelta

import pytest


@pytest.fixture
def seeded_user(tmp_db):
    """A real ``User`` row so the join in ``_select_with_owner`` resolves."""
    from app.auth import users as users_repo
    return users_repo.create(email="agent-user@x.com", password="hunter2-x", name="Author")


def test_upsert_creates_then_renews(seeded_user):
    from app.wiki import agent_activity

    e1 = agent_activity.upsert_activity(
        user_id=seeded_user, agent_name=None, doc_path="guide.md",
        activity="read", description=None,
    )
    e2 = agent_activity.upsert_activity(
        user_id=seeded_user, agent_name=None, doc_path="guide.md",
        activity="read", description=None,
        ttl=timedelta(hours=48),
    )
    # Same natural key ⇒ second call slides expires_at, doesn't insert.
    rows = agent_activity.list_for_doc("guide.md")
    assert len(rows) == 1
    assert rows[0].expires_at == e2
    assert e2 > e1  # 48h horizon is later than 24h


def test_upsert_distinct_agent_names_are_separate_rows(seeded_user):
    """``agent_name=None`` and ``agent_name="x"`` are distinct via NULLS NOT DISTINCT."""
    from app.wiki import agent_activity

    agent_activity.upsert_activity(
        user_id=seeded_user, agent_name=None, doc_path="guide.md",
        activity="read", description=None,
    )
    agent_activity.upsert_activity(
        user_id=seeded_user, agent_name="alpha", doc_path="guide.md",
        activity="read", description=None,
    )
    rows = agent_activity.list_for_doc("guide.md")
    assert len(rows) == 2
    names = sorted([r.agent_name for r in rows], key=lambda x: (x is not None, x or ""))
    assert names == [None, "alpha"]


def test_upsert_rejects_unknown_activity(seeded_user):
    from app.wiki import agent_activity

    with pytest.raises(ValueError):
        agent_activity.upsert_activity(
            user_id=seeded_user, agent_name=None, doc_path="x.md",
            activity="deleted", description=None,
        )


def test_list_for_doc_filters_expired(seeded_user):
    from app.wiki import agent_activity

    agent_activity.upsert_activity(
        user_id=seeded_user, agent_name="live", doc_path="x.md",
        activity="read", description=None, ttl=timedelta(hours=1),
    )
    # An already-expired row with TTL in the past — list_for_doc must hide it.
    agent_activity.upsert_activity(
        user_id=seeded_user, agent_name="dead", doc_path="x.md",
        activity="read", description=None, ttl=timedelta(seconds=-60),
    )
    rows = agent_activity.list_for_doc("x.md")
    names = [r.agent_name for r in rows]
    assert names == ["live"]
    # list_all_expired sees the dead one (and not the live one).
    expired_names = [r.agent_name for r in agent_activity.list_all_expired()]
    assert "dead" in expired_names
    assert "live" not in expired_names


def test_get_and_delete_by_natural_key_round_trip(seeded_user):
    from app.wiki import agent_activity

    agent_activity.upsert_activity(
        user_id=seeded_user, agent_name=None, doc_path="x.md",
        activity="wrote", description="initial",
    )
    row = agent_activity.get_by_natural_key(
        user_id=seeded_user, agent_name=None, doc_path="x.md", activity="wrote",
    )
    assert row is not None
    assert row.description == "initial"

    agent_activity.delete_by_natural_key(
        user_id=seeded_user, agent_name=None, doc_path="x.md", activity="wrote",
    )
    assert agent_activity.get_by_natural_key(
        user_id=seeded_user, agent_name=None, doc_path="x.md", activity="wrote",
    ) is None


def test_delete_for_doc_removes_all_activities_on_path(seeded_user):
    """A doc deletion should clear every row attached to that path,
    regardless of agent name or activity kind.
    """
    from app.wiki import agent_activity

    agent_activity.upsert_activity(
        user_id=seeded_user, agent_name=None, doc_path="x.md",
        activity="read", description=None,
    )
    agent_activity.upsert_activity(
        user_id=seeded_user, agent_name="a", doc_path="x.md",
        activity="wrote", description="touched",
    )
    agent_activity.upsert_activity(
        user_id=seeded_user, agent_name=None, doc_path="other.md",
        activity="read", description=None,
    )

    agent_activity.delete_for_doc("x.md")
    assert agent_activity.list_for_doc("x.md") == []
    # Unrelated doc's row is untouched.
    assert len(agent_activity.list_for_doc("other.md")) == 1


def test_rename_doc_repoints_existing_rows(seeded_user):
    from app.wiki import agent_activity

    agent_activity.upsert_activity(
        user_id=seeded_user, agent_name=None, doc_path="old.md",
        activity="read", description=None,
    )
    agent_activity.rename_doc("old.md", "new.md")
    assert agent_activity.list_for_doc("old.md") == []
    assert len(agent_activity.list_for_doc("new.md")) == 1


def test_list_for_doc_owner_display_falls_back_to_email(tmp_db):
    """``owner_display`` is ``COALESCE(name, email)``; a user with no
    name should surface the email so the UI / API doesn't show
    NULL."""
    from app.auth import users as users_repo
    from app.wiki import agent_activity

    uid = users_repo.create(email="nameless@x.com", password="hunter2-x", name=None)
    agent_activity.upsert_activity(
        user_id=uid, agent_name=None, doc_path="x.md",
        activity="read", description=None,
    )
    rows = agent_activity.list_for_doc("x.md")
    assert len(rows) == 1
    assert rows[0].owner_display == "nameless@x.com"
