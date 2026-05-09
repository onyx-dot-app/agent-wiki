"""Smoke test for the integration harness itself.

Exercises the full stack: real Postgres (per-test schema), real wiki git
repo, real Flask app, real pgmq queues in immediate mode, scripted LLM.
If this passes, the foundation is wired correctly; flow-specific tests
live alongside.
"""
from __future__ import annotations


def test_signup_and_save_doc_and_search(integration):
    """Signup → save → BM25 search round-trip via the real API."""
    integration.signup_and_signin(email="u@x.com")

    integration.put_doc("guide.md", "# Bcrypt Guide\n\nwe use bcrypt for password hashing\n")
    integration.put_doc("auth.md", "# Auth\n\nsessions are signed with HMAC\n")

    # Search hits the BM25 index (pg_textsearch) and the Python-side snippet path.
    from app.db import fts
    hits = fts.search("bcrypt")
    assert any(h.path == "guide.md" for h in hits), hits
    assert "**bcrypt**" in next(h for h in hits if h.path == "guide.md").snippet


def test_trigger_fires_when_llm_says_match(integration):
    """Trigger creation → matching commit → trigger.fire event in the audit log.

    The scripted LLM stands in for the natural-language match + render
    calls; the rest of the path (DB lookup, fan-out task, event row
    insert) runs for real. Both calls are tool-call shaped — phase 1
    uses the ``report`` tool, phase 2 uses ``render``.
    """
    integration.signup_and_signin()

    integration.create_trigger(
        scope_path="status.md",
        condition="status changes from green to anything else",
        message="Status flipped on status.md",
    )

    # Phase 1: the ``report`` tool says it matched.
    integration.llm.respond(
        when=lambda c: any(t["name"] == "report" for t in (c.get("tools") or [])),
        tool_calls=[{"id": "tc1", "name": "report",
                     "arguments": {"matches": True, "reason": "green→yellow"}}],
    )
    # Phase 2: the ``render`` tool returns the final delivered message.
    integration.llm.respond(
        when=lambda c: any(t["name"] == "render" for t in (c.get("tools") or [])),
        tool_calls=[{"id": "tc2", "name": "render",
                     "arguments": {"message": "Status flipped on status.md"}}],
    )

    integration.put_doc("status.md", "# Status\n\nstatus: green\n")
    integration.put_doc("status.md", "# Status\n\nstatus: yellow\n")

    fires = integration.fired_triggers()
    assert fires, "expected at least one trigger.fire event"
    assert fires[0]["kind"] == "trigger.fire"
    assert fires[0]["payload"]["message"] == "Status flipped on status.md"


def test_default_llm_response_is_safe(integration):
    """A test that doesn't script the LLM gets a benign empty response —
    the trigger evaluator can run through without raising.
    """
    integration.signup_and_signin()
    integration.create_trigger(
        scope_path="x.md", condition="never matches", message="x"
    )
    integration.put_doc("x.md", "# X\n\nbody\n")
    # No fires expected; the empty-text default doesn't parse as a match.
    assert integration.fired_triggers() == []
