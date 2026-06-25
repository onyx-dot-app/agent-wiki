"""Tests for GET /api/wiki/update-health — the raw auto-update facts (24h
count, resolved threshold, cap) that back the threshold slider and the
client-side too-frequent-update banner."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import users as users_repo
from app.ingest import settings as ingest_settings
from app.main import create_app
from app.wiki import git as wiki_git
from app.wiki import update_policy
from app.wiki import utils as wiki_utils
from tests._auth import login_fastapi

PATH = "team/page.md"


@pytest.fixture
def client(tmp_db: None, tmp_repo: None) -> TestClient:
    return TestClient(create_app())


def test_unauthenticated_is_401(client: TestClient) -> None:
    assert client.get(f"/api/wiki/update-health?path={PATH}").status_code == 401


def test_returns_count_threshold_and_cap(client: TestClient) -> None:
    uid = users_repo.create(email="o@x.com", password="hunter2-x", name="O")
    login_fastapi(client, uid)
    for i in range(3):
        wiki_git.commit_file(PATH, f"b{i}\n", "ingest", author=wiki_utils.INGEST_AUTHOR)
    update_policy.set_policy(PATH, warn_update_threshold=2)

    body = client.get(f"/api/wiki/update-health?path={PATH}").json()
    assert body["count_24h"] == 3
    assert body["threshold_24h"] == 2  # explicit per-page value
    assert "cap_24h" in body  # admin cap, for the slider max


def test_threshold_falls_back_to_workspace_default(client: TestClient) -> None:
    uid = users_repo.create(email="o@x.com", password="hunter2-x", name="O")
    login_fastapi(client, uid)
    # No per-page row → threshold is the workspace default.
    body = client.get(f"/api/wiki/update-health?path={PATH}").json()
    assert body["threshold_24h"] == ingest_settings.DEFAULT_WARN_UPDATE_THRESHOLD
