"""Tests for the too-frequent-update guardrail (app/tasks/update_frequency.py).

The guardrail records a ``wiki.frequent_updates`` activity event on the commit
that crosses a page's warning threshold — once per crossing, not per commit.
"""

from __future__ import annotations

import pytest

from app.tasks.update_frequency import (
    EVENT_FREQUENT_UPDATES,
    _check_update_frequency_inline,
)
from app.wiki import constants as wiki_constants
from app.wiki import git as wiki_git
from app.wiki import update_policy
from tests._seed import list_events

PATH = "team/page.md"


@pytest.fixture(autouse=True)
def _db(tmp_db: None, tmp_repo: None) -> None:
    return None


def _ingest_commit_and_check(n: int) -> None:
    """One ingestion commit, then the post-commit frequency check — mirrors the
    real per-commit flow (after_doc_write enqueues a check per commit)."""
    wiki_git.commit_file(PATH, f"body {n}\n", "ingest", author=wiki_constants.INGEST_AUTHOR)
    _check_update_frequency_inline(PATH)


def _events() -> list[dict]:
    return [e for e in list_events(EVENT_FREQUENT_UPDATES) if e["target"] == PATH]


def test_below_threshold_records_nothing() -> None:
    update_policy.set_policy(PATH, warn_update_threshold=5)
    for n in range(3):
        _ingest_commit_and_check(n)
    assert _events() == []


def test_records_event_on_crossing() -> None:
    update_policy.set_policy(PATH, warn_update_threshold=3)
    for n in range(3):
        _ingest_commit_and_check(n)
    evs = _events()
    assert len(evs) == 1
    assert evs[0]["payload"]["count"] == 3
    assert evs[0]["payload"]["threshold"] == 3


def test_no_duplicate_while_over_threshold() -> None:
    update_policy.set_policy(PATH, warn_update_threshold=3)
    # Four commits, each followed by its check: the event fires only on the
    # crossing commit (count == 3), not again at 4.
    for n in range(4):
        _ingest_commit_and_check(n)
    assert len(_events()) == 1


def test_threshold_zero_warns_every_update() -> None:
    # Threshold 0 means "warn on every auto-update" (not off) — one event per
    # ingestion commit.
    update_policy.set_policy(PATH, warn_update_threshold=0)
    for n in range(3):
        _ingest_commit_and_check(n)
    assert len(_events()) == 3
