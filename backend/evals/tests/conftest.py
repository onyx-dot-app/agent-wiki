"""Lightweight pytest conftest for the eval-package unit tests.

These tests cover pure functions (scorers, reporting math, table rendering)
plus the dry-run stub. They do NOT need Postgres, OpenSearch, the wiki
repo, or the FastAPI app. Kept in a sibling tree so they don't pull in
``tests/conftest.py``'s DB-per-test setup.
"""

from __future__ import annotations
