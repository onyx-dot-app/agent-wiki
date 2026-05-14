"""Flow 4 — external ingest updates a wiki doc via the document-updater agent.

Skipped pending the consumer task. ``process_pushed_document`` (and the
helper ``update_document_from_payload``) raise ``NotImplementedError``;
under ``immediate_queues`` the ingest endpoint queues a task that blows
up synchronously. Once the task lands this test drops in with no harness
changes — the LLM seam, queue mode, and event-log helper already exist.
"""
from __future__ import annotations

import pytest


@pytest.mark.skip(
    reason="document-updater task not implemented; "
    "see app/tasks/wiki_update.py:process_pushed_document"
)
def test_ingest_updates_wiki_doc(integration):
    integration.signup_and_signin()
    integration.put_doc(
        "topics/auth.md", "# Auth\n\noriginal body about sessions\n"
    )

    # Future shape: phase 1 picks the page, phase 2 produces the new body.
    integration.llm.respond(
        when=lambda c: any(t.get("name") == "select_page"
                           for t in (c.get("tools") or [])),
        tool_calls=[{"id": "tc1", "name": "select_page",
                     "arguments": {"path": "topics/auth.md"}}],
    )
    integration.llm.respond(
        when=lambda c: any(t.get("name") == "edit_doc"
                           for t in (c.get("tools") or [])),
        tool_calls=[{"id": "tc2", "name": "edit_doc",
                     "arguments": {"path": "topics/auth.md",
                                   "new_body": "# Auth\n\nupdated body about token rotation\n"}}],
    )

    resp = integration.client.post(
        "/api/wiki/ingest",
        json={"content": "we now use rotating refresh tokens",
              "source_type": "test", "title": "Auth notes"},
    )
    assert resp.status_code == 202

    from app.wiki import git as wiki_git
    body = wiki_git.read_file("topics/auth.md")
    assert "token rotation" in body

    from app.db import fts
    assert any(h.path == "topics/auth.md"
               for h in fts.search("rotation"))

    fires = integration.events(kind="document.agent_edit")
    assert fires, fires
