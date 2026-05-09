"""Negative paths that protect the doc-edit invariants.

These exercise rules the codebase relies on but that are hard to spot
via unit tests alone — they need a real DB, a real wiki repo, and the
agent-activity registry wired up:

* The ``agents:`` frontmatter block is registry-managed; an agent
  body that mutates it must be rejected before commit.
* Doc-edit tools must refuse to overwrite a doc the model hasn't
  read this turn (the read-before-write guard).
"""
from __future__ import annotations


def _enter_request_with_user(flask_app, uid: str):
    """Push a Flask request context with ``session["user_id"]`` set."""
    ctx = flask_app.test_request_context()
    ctx.push()
    from flask import session as flask_session
    flask_session["user_id"] = uid
    return ctx


def test_write_doc_rejects_agents_block_mutation(
    integration, flask_app, monkeypatch
):
    """An agent that submits a body editing the registry-managed
    ``agents:`` block gets a ToolError back, the doc on disk is
    untouched, and no extra commit lands.
    """
    # Skip the cleanup task so the registry row stays put for the read,
    # giving us a real `agents:` block on disk to try to tamper with.
    monkeypatch.setattr(
        "app.tasks.agent_activity.schedule_cleanup_for_natural_key",
        lambda **kw: None,
    )

    uid = integration.signup_and_signin(email="agent-user@x.com")
    integration.put_doc("guide.md", "# Guide\n\noriginal body\n")

    from app.llm.agents.tools import read_page, write_doc
    from app.llm.agents import _session
    from app.wiki import agent_activity, git as wiki_git

    token = agent_activity.agent_name_var.set("status-watcher")
    seen_token = _session.seen_doc_paths.set({"guide.md"})
    ctx = _enter_request_with_user(flask_app, uid)
    try:
        # First read — stamps the frontmatter on disk.
        read_page.handle({"path": "guide.md"})
        before = wiki_git.read_file("guide.md")
        assert before.startswith("---\n"), before

        sha_before = wiki_git.head_sha_for_path("guide.md")

        # Tamper: rewrite the body with a fake `agents:` entry.
        tampered = (
            "---\n"
            "agents:\n"
            "  - owner: attacker\n"
            "    agent: malicious\n"
            "    activity: read\n"
            "    description: spoofed\n"
            "    expires_at: '2099-01-01T00:00:00+00:00'\n"
            "---\n"
            "# Guide\n\nspoofed body\n"
        )
        result = write_doc.handle({
            "path": "guide.md",
            "body": tampered,
            "commit_message": "spoof",
        })

        assert "error" in result, result
        assert "agents" in result["error"].lower()

        # Disk is unchanged — no spoofed body, no new commit.
        after = wiki_git.read_file("guide.md")
        assert after == before
        assert wiki_git.head_sha_for_path("guide.md") == sha_before
    finally:
        ctx.pop()
        _session.seen_doc_paths.reset(seen_token)
        agent_activity.agent_name_var.reset(token)


def test_write_doc_rejects_when_path_not_read(
    integration, flask_app, monkeypatch
):
    """An agent that hasn't read a doc this turn cannot overwrite it,
    even if a parallel HTTP path would have allowed it.
    """
    monkeypatch.setattr(
        "app.tasks.agent_activity.schedule_cleanup_for_natural_key",
        lambda **kw: None,
    )

    uid = integration.signup_and_signin(email="forgetful@x.com")
    integration.put_doc("guide.md", "# Guide\n\noriginal body\n")

    from app.llm.agents.tools import write_doc
    from app.llm.agents import _session
    from app.wiki import git as wiki_git

    sha_before = wiki_git.head_sha_for_path("guide.md")

    # Empty `seen_doc_paths` set ⇒ "we're inside a chat loop, but the
    # model has read nothing." Default `None` would no-op the guard.
    seen_token = _session.seen_doc_paths.set(set())
    ctx = _enter_request_with_user(flask_app, uid)
    try:
        result = write_doc.handle({
            "path": "guide.md",
            "body": "# Guide\n\nwholesale rewrite\n",
            "commit_message": "blind overwrite",
        })
        assert "error" in result, result
        assert "read_page" in result["error"]

        # Disk is unchanged.
        assert wiki_git.head_sha_for_path("guide.md") == sha_before
        assert "wholesale rewrite" not in wiki_git.read_file("guide.md")
    finally:
        ctx.pop()
        _session.seen_doc_paths.reset(seen_token)


def test_write_doc_allowed_when_path_was_read(
    integration, flask_app, monkeypatch
):
    """Positive complement: with the path in ``seen_doc_paths`` the
    same write goes through. Without this, a regression that always
    rejected writes would still pass the negative test above.
    """
    monkeypatch.setattr(
        "app.tasks.agent_activity.schedule_cleanup_for_natural_key",
        lambda **kw: None,
    )

    uid = integration.signup_and_signin(email="thorough@x.com")
    integration.put_doc("guide.md", "# Guide\n\noriginal body\n")

    from app.llm.agents.tools import write_doc
    from app.llm.agents import _session
    from app.wiki import git as wiki_git

    seen_token = _session.seen_doc_paths.set({"guide.md"})
    ctx = _enter_request_with_user(flask_app, uid)
    try:
        result = write_doc.handle({
            "path": "guide.md",
            "body": "# Guide\n\nwholesale rewrite\n",
            "commit_message": "informed overwrite",
        })
        assert "error" not in result, result
        assert "wholesale rewrite" in wiki_git.read_file("guide.md")
    finally:
        ctx.pop()
        _session.seen_doc_paths.reset(seen_token)
