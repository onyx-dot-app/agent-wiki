"""Tests for git.count_commits_since — the ingestion-update counter that backs
the Update Policy panel's 24h count, the threshold warning, and update-health."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.wiki import constants as wiki_constants
from app.wiki import git as wiki_git

INGEST = wiki_constants.INGEST_AUTHOR
EMAIL = wiki_constants.INGEST_AUTHOR_EMAIL
HUMAN_AUTHOR = "Nik <nik@x.com>"


@pytest.fixture(autouse=True)
def _repo(tmp_repo: None) -> None:
    return None


def _recent() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()


def _seed() -> None:
    # team/a.md: two ingest updates + one human edit (human must not count).
    wiki_git.commit_file("team/a.md", "a1\n", "ingest", author=INGEST)
    wiki_git.commit_file("team/a.md", "a2\n", "ingest", author=INGEST)
    wiki_git.commit_file("team/a.md", "a3 human\n", "edit", author=HUMAN_AUTHOR)
    # team/b.md: one ingest update (a folder count aggregates it with a.md).
    wiki_git.commit_file("team/b.md", "b1\n", "ingest", author=INGEST)
    # other/c.md: one ingest update outside the team/ folder.
    wiki_git.commit_file("other/c.md", "c1\n", "ingest", author=INGEST)


def test_counts_only_ingest_commits_for_a_page() -> None:
    _seed()
    assert wiki_git.count_commits_since("team/a.md", author=EMAIL, since_iso=_recent()) == 2


def test_folder_path_aggregates_pages_beneath_it() -> None:
    _seed()
    # team/ holds a.md (2 ingest) + b.md (1 ingest); other/c.md is excluded.
    assert wiki_git.count_commits_since("team", author=EMAIL, since_iso=_recent()) == 3


def test_empty_path_counts_whole_repo() -> None:
    _seed()
    assert wiki_git.count_commits_since("", author=EMAIL, since_iso=_recent()) == 4


def test_author_match_is_anchored_not_substring() -> None:
    # A lookalike whose email merely contains the ingest email as a prefix must
    # not be counted — the match is anchored to the exact <email>.
    wiki_git.commit_file("team/a.md", "x\n", "edit", author="Imposter <onyx-ingest@localhost>")
    wiki_git.commit_file("team/a.md", "y\n", "ingest", author=INGEST)
    assert wiki_git.count_commits_since("team/a.md", author=EMAIL, since_iso=_recent()) == 1


def test_window_excludes_commits_before_since() -> None:
    _seed()
    # A since in the future matches nothing.
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert wiki_git.count_commits_since("team/a.md", author=EMAIL, since_iso=future) == 0
