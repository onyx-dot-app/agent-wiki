"""Negative paths for trigger fan-out — proves a fire is the exception.

These cover the silent-regression hazards: if any of the rules below
broke, the visible symptom in production would be either a flood of
spurious notifications or a swallowed exception in the worker. Each
test asserts both the absence of a `trigger.fire` row and (where
relevant) that no second LLM call was made.
"""
from __future__ import annotations


def _seed_doc_trigger(integration, *, scope_path: str = "status.md") -> str:
    integration.signup_and_signin()
    return integration.create_trigger(
        scope_path=scope_path,
        condition="status changes from green",
        message="Status flipped",
    )


def test_phase1_false_does_not_render_or_fire(integration):
    """When `report` says no, the renderer must never run and no event lands."""
    _seed_doc_trigger(integration)
    integration.llm.respond(
        when=lambda c: any(t["name"] == "report" for t in (c.get("tools") or [])),
        tool_calls=[{"id": "tc1", "name": "report",
                     "arguments": {"matches": False, "reason": "no signal"}}],
    )

    integration.put_doc("status.md", "# Status\n\nstatus: green\n")
    integration.put_doc("status.md", "# Status\n\nstatus: green still\n")

    assert integration.fired_triggers() == []
    # Doc-scoped triggers use the standard delta path even on create (the
    # new-file-in-dir branch only kicks in for directory scopes), so eval
    # ran twice. The render path must never have been reached.
    eval_calls = [c for c in integration.llm.calls
                  if any(t["name"] == "report" for t in (c.get("tools") or []))]
    render_calls = [c for c in integration.llm.calls
                    if any(t["name"] == "render" for t in (c.get("tools") or []))]
    assert len(eval_calls) == 2, eval_calls
    assert render_calls == [], render_calls


def test_disabled_trigger_does_not_fire(integration):
    """`enabled=false` must short-circuit before the LLM is even called."""
    tid = _seed_doc_trigger(integration)
    resp = integration.client.put(f"/api/triggers/{tid}", json={"enabled": False})
    assert resp.status_code == 200, resp.get_data(as_text=True)

    # Script a definite "yes" — if the disabled path leaks, we'll see a fire.
    integration.llm.respond(
        when=lambda c: any(t["name"] == "report" for t in (c.get("tools") or [])),
        tool_calls=[{"id": "tc1", "name": "report",
                     "arguments": {"matches": True, "reason": "would-have-matched"}}],
    )
    integration.llm.respond(
        when=lambda c: any(t["name"] == "render" for t in (c.get("tools") or [])),
        tool_calls=[{"id": "tc2", "name": "render",
                     "arguments": {"message": "should not arrive"}}],
    )

    integration.put_doc("status.md", "# Status\n\nstatus: green\n")
    integration.put_doc("status.md", "# Status\n\nstatus: red\n")

    assert integration.fired_triggers() == []
    # Disabled rows are filtered in find_matching_triggers, so the LLM
    # is never reached at all.
    assert integration.llm.calls == [], integration.llm.calls


def test_llm_error_does_not_fire_or_crash(integration):
    """Provider failure during phase 1 must drop the fire silently — the
    PUT still succeeds, no event row is written, no exception escapes.
    """
    from app.llm.errors import LLMError
    _seed_doc_trigger(integration)
    integration.llm.raise_for(
        LLMError(code="provider_down", message="boom"),
        when=lambda c: any(t["name"] == "report" for t in (c.get("tools") or [])),
    )

    integration.put_doc("status.md", "# Status\n\nstatus: green\n")
    # The doc still committed.
    from app.wiki import git as wiki_git
    assert "status: green" in wiki_git.read_file("status.md")
    assert integration.fired_triggers() == []


def test_unparseable_new_file_response_does_not_fire(integration):
    """Garbage from the new-file-in-dir LLM must drop, not crash or fire."""
    integration.signup_and_signin()
    integration.create_trigger(
        scope_path="reports",
        condition="any new doc",
        message="should not deliver",
    )
    # New-file-in-dir uses no tools and parses JSON from text. Junk ⇒ no fire.
    integration.llm.respond(
        when=lambda c: not c.get("tools"),
        text="not json at all, sorry",
    )

    integration.put_doc("reports/q1.md", "# Q1\n\nanything\n")
    assert integration.fired_triggers() == []
