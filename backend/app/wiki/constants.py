"""Leaf constants for the wiki package — no imports, so any module can read
these without pulling in a heavy dependency chain.

The ingest git-identity below is needed by both ``app/wiki/utils.py`` (which
binds it on the ingest path via ``system_author``) and leaf consumers like
``app/tasks/update_frequency.py`` and ``app/wiki/notify.py``. Keeping it here
rather than in ``utils.py`` avoids a cycle: ``utils`` imports
``llm.agents.tools`` → ``notify``, so a constant living in ``utils`` couldn't be
read by ``notify`` / ``update_frequency`` without an import loop.
"""
from __future__ import annotations

# Git identity for connector-pushed ingestion commits. Bound on the ingest path
# via ``utils.system_author(INGEST_AUTHOR)``; the email is what callers filter on
# when counting ingestion commits (``count_commits_since``).
INGEST_AUTHOR_EMAIL = "onyx-ingest@local"
INGEST_AUTHOR = f"Onyx Ingest <{INGEST_AUTHOR_EMAIL}>"
