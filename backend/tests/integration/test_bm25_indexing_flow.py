"""Flow 3 — BM25 index picks up a new doc.

Broken out from the smoke test so the index path is asserted on its
own. With ``immediate_queues``, ``reindex_path`` has already run by the
time the PUT returns. Nothing about this path goes through the LLM.
"""
from __future__ import annotations


def test_put_doc_lands_in_bm25(integration):
    integration.signup_and_signin()
    integration.put_doc(
        "guide.md", "# Bcrypt Guide\n\nwe use bcrypt for password hashing\n"
    )

    from app.db import fts
    hits = fts.search("bcrypt")
    assert any(h.path == "guide.md" for h in hits), hits
    snippet = next(h for h in hits if h.path == "guide.md").snippet
    assert "**bcrypt**" in snippet, snippet


def test_edit_replaces_bm25_row(integration):
    integration.signup_and_signin()
    integration.put_doc("notes.md", "# Notes\n\nzeppelins are quiet airships\n")

    from app.db import fts
    assert any(h.path == "notes.md" for h in fts.search("zeppelins"))

    integration.put_doc("notes.md", "# Notes\n\nfrigates are fast warships\n")
    # old term gone, new term present — single-row reindex on edit.
    assert not any(h.path == "notes.md" for h in fts.search("zeppelins"))
    assert any(h.path == "notes.md" for h in fts.search("frigates"))
