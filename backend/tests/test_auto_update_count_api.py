"""Tests for GET /api/wiki/auto-update-count — the 24h ingestion-update count
surfaced in the Update Policy panel."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.auth import users as users_repo
from app.main import create_app
from app.wiki import git as wiki_git
from app.wiki import utils as wiki_utils
from tests._auth import login_fastapi

HUMAN_AUTHOR = "Nik <nik@x.com>"


@pytest.fixture
def client(tmp_db: None, tmp_repo: None) -> TestClient:
    return TestClient(create_app())


def _seed(client: TestClient) -> None:
    uid = users_repo.create(email="nik@x.com", password="hunter2-x", name="Nik")
    login_fastapi(client, uid)
    ingest = wiki_utils.INGEST_AUTHOR
    # team/a.md: two ingest updates + one human edit (human must not count).
    wiki_git.commit_file("team/a.md", "a1\n", "ingest", author=ingest)
    wiki_git.commit_file("team/a.md", "a2\n", "ingest", author=ingest)
    wiki_git.commit_file("team/a.md", "a3 human\n", "edit", author=HUMAN_AUTHOR)
    # team/b.md: one ingest update (folder count should aggregate it with a.md).
    wiki_git.commit_file("team/b.md", "b1\n", "ingest", author=ingest)
    # other/c.md: one ingest update outside the team/ folder.
    wiki_git.commit_file("other/c.md", "c1\n", "ingest", author=ingest)


def test_unauthenticated_is_401(client: TestClient) -> None:
    assert client.get("/api/wiki/auto-update-count?path=team/a.md").status_code == 401


def test_counts_ingest_commits_for_a_page(client: TestClient) -> None:
    _seed(client)
    body = client.get("/api/wiki/auto-update-count?path=team/a.md").json()
    assert body == {"path": "team/a.md", "hours": 24, "count": 2}


def test_folder_path_aggregates_pages_beneath_it(client: TestClient) -> None:
    _seed(client)
    # team/ holds a.md (2 ingest) + b.md (1 ingest); other/c.md is excluded.
    body = client.get("/api/wiki/auto-update-count?path=team").json()
    assert body["count"] == 3


def test_root_path_counts_whole_repo(client: TestClient) -> None:
    _seed(client)
    body = client.get("/api/wiki/auto-update-count").json()
    assert body["path"] == ""
    assert body["count"] == 4  # all four ingest commits, no human edit


def test_window_excludes_commits_before_since(client: TestClient) -> None:
    _seed(client)
    # The window is committer-date based; a since in the future matches nothing.
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert (
        wiki_git.count_commits_since(
            "team/a.md", author=wiki_utils.INGEST_AUTHOR_EMAIL, since_iso=future
        )
        == 0
    )
