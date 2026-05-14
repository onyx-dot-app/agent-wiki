"""Flow 2 — folder-scoped trigger fires on create + edit.

Two LLM seam shapes, picked by ``fan_out_trigger_eval``:
  * ``change_kind="create"`` under a directory scope → the
    ``evaluate_new_file_in_dir`` path, a single ``complete()`` call with
    NO tools that emits ``{"triggered": ..., "trigger_message": ...}`` in
    the assistant text.
  * ``change_kind="edit"`` → standard delta path: two tool calls
    (``report`` then ``render``).

Script by call shape (tools list / tool name) the way the smoke test
does for Flow 1.
"""
from __future__ import annotations


def test_dir_trigger_fires_on_create_then_edit(integration):
    integration.signup_and_signin()

    integration.client.post("/api/wiki/folder", json={"path": "reports"})

    integration.create_trigger(
        scope_path="reports",
        condition="any new or updated quarterly status doc",
        message="reports/ updated",
    )

    # Phase A: new-file-in-dir path. complete() is called with no tools
    # and we return a JSON text body the natural-language module parses.
    integration.llm.respond(
        when=lambda c: not c.get("tools"),
        text='{"triggered": true, "trigger_message": "first report dropped"}',
    )
    # Phase B: standard delta path on the subsequent edit — report + render.
    integration.llm.respond(
        when=lambda c: any(t["name"] == "report" for t in (c.get("tools") or [])),
        tool_calls=[{"id": "tc1", "name": "report",
                     "arguments": {"matches": True, "reason": "edit under dir scope"}}],
    )
    integration.llm.respond(
        when=lambda c: any(t["name"] == "render" for t in (c.get("tools") or [])),
        tool_calls=[{"id": "tc2", "name": "render",
                     "arguments": {"message": "report edited"}}],
    )

    integration.put_doc("reports/q1.md", "# Q1\n\nstatus: green\n")
    fires_after_create = integration.fired_triggers()
    assert len(fires_after_create) == 1, fires_after_create
    assert fires_after_create[0]["payload"]["change_kind"] == "create"
    assert fires_after_create[0]["payload"]["message"] == "first report dropped"

    integration.put_doc("reports/q1.md", "# Q1\n\nstatus: yellow\n")
    fires_after_edit = integration.fired_triggers()
    assert len(fires_after_edit) == 2, fires_after_edit
    # newest-first
    assert fires_after_edit[0]["payload"]["change_kind"] == "edit"
    assert fires_after_edit[0]["payload"]["message"] == "report edited"
