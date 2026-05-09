"""Flow 5 — agent read stamps the doc's `agents:` frontmatter.

The agent-activity registry is the source of truth; every wiki ``.md``
carries a managed ``agents:`` YAML block rendered from it. When an
agent calls ``read_page`` on a doc, ``mark_doc_read`` upserts a
``read`` row and re-commits the doc with the new frontmatter.

This test drives the real ``read_page`` handler inside a Flask request
context so ``current_user()`` resolves; the rest of the path (DB
upsert, frontmatter render, commit, reindex) runs for real. We assert
the doc on disk now carries an ``agents:`` block with the user's
display name, the agent name set on ``agent_name_var``, ``activity:
read``, and an ``expires_at`` ~24h in the future.

Caveat: under ``immediate_queues`` every scheduled task runs
synchronously, ``eta`` and all. ``mark_doc_read`` schedules a 24h-out
cleanup that would otherwise delete the row before the frontmatter
gets rendered, so the test stubs ``schedule_cleanup_for_natural_key``
to a no-op. In production the eta keeps the cleanup pending.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import yaml


def test_agent_read_stamps_frontmatter(integration, flask_app, monkeypatch):
    monkeypatch.setattr(
        "app.tasks.agent_activity.schedule_cleanup_for_natural_key",
        lambda **kw: None,
    )

    uid = integration.signup_and_signin(email="agent-user@x.com")

    # Seed a doc with no frontmatter — pure body.
    integration.put_doc("guide.md", "# Guide\n\noriginal body\n")

    from app.llm.agents.tools import read_page
    from app.wiki import agent_activity, git as wiki_git

    before = wiki_git.read_file("guide.md")
    assert not before.startswith("---\n"), "doc starts with no frontmatter"

    # Drive the tool the way the chat loop does: a Flask request context
    # carrying the signed-in user's session, plus the per-turn agent name.
    token = agent_activity.agent_name_var.set("status-watcher")
    try:
        with flask_app.test_request_context():
            from flask import session as flask_session
            flask_session["user_id"] = uid

            t0 = datetime.now(timezone.utc)
            result = read_page.handle({"path": "guide.md"})
            t1 = datetime.now(timezone.utc)
    finally:
        agent_activity.agent_name_var.reset(token)

    assert "error" not in result, result
    assert "original body" in result["body"]

    after = wiki_git.read_file("guide.md")
    fm_text, rest = agent_activity.split_frontmatter(after)
    assert fm_text is not None, f"expected frontmatter, got: {after!r}"
    assert "original body" in rest, "body must survive the rewrite"

    fm = yaml.safe_load(fm_text)
    agents = fm["agents"]
    assert len(agents) == 1, agents
    entry = agents[0]
    # Harness signup sets name="U"; owner_display = coalesce(name, email).
    assert entry["owner"] == "U"
    assert entry["agent"] == "status-watcher"
    assert entry["activity"] == "read"
    assert entry["description"] in (None, "N/A")

    # PyYAML auto-converts ISO timestamps to ``datetime`` (UTC-aware here
    # because the rendered value carries ``+00:00``).
    expires = entry["expires_at"]
    assert isinstance(expires, datetime), expires
    # Default TTL is 24h; allow a generous window around the test interval.
    expected_min = t0 + timedelta(hours=24) - timedelta(seconds=5)
    expected_max = t1 + timedelta(hours=24) + timedelta(seconds=5)
    assert expected_min <= expires <= expected_max, (
        f"expires_at {expires} outside window [{expected_min}, {expected_max}]"
    )


def test_agent_read_anonymous_renders_na(integration, flask_app, monkeypatch):
    """No ``agent_name_var`` set → entry shows ``agent: N/A``."""
    monkeypatch.setattr(
        "app.tasks.agent_activity.schedule_cleanup_for_natural_key",
        lambda **kw: None,
    )

    uid = integration.signup_and_signin(email="solo@x.com")
    integration.put_doc("notes.md", "# Notes\n\nbody\n")

    from app.llm.agents.tools import read_page
    from app.wiki import agent_activity, git as wiki_git

    with flask_app.test_request_context():
        from flask import session as flask_session
        flask_session["user_id"] = uid
        read_page.handle({"path": "notes.md"})

    body = wiki_git.read_file("notes.md")
    fm_text, _ = agent_activity.split_frontmatter(body)
    assert fm_text is not None
    fm = yaml.safe_load(fm_text)
    assert fm["agents"][0]["agent"] == "N/A"
    assert fm["agents"][0]["owner"] == "U"
