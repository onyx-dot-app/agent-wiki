"""Tests for GET /api/wiki/update-health — the raw auto-update facts (24h
count, resolved threshold, cap) that back the threshold slider and the
client-side too-frequent-update banner."""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.auth import users as users_repo
from app.ingest import settings as ingest_settings
from app.main import create_app
from app.wiki import acl
from app.wiki import constants as wiki_constants
from app.wiki import git as wiki_git
from app.wiki import update_policy
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
        wiki_git.commit_file(PATH, f"b{i}\n", "ingest", author=wiki_constants.INGEST_AUTHOR)
    update_policy.set_policy(PATH, warn_update_threshold=2)

    body = client.get(f"/api/wiki/update-health?path={PATH}").json()
    assert body["count_24h"] == 3
    assert body["threshold_24h"] == 2  # explicit per-page value
    assert "cap_24h" in body  # admin cap, for the slider max
    assert body["can_manage"] is True  # first user is admin → can act on the warning
    assert body["cap_resets_at"] is None  # not over the cap


def test_cap_resets_at_set_when_over_cap(client: TestClient) -> None:
    uid = users_repo.create(email="o@x.com", password="hunter2-x", name="O")
    login_fastapi(client, uid)
    ingest_settings.upsert(max_doc_chars=100_000, onyx_base_url=None, auto_update_cap=2)
    for i in range(3):
        wiki_git.commit_file(PATH, f"b{i}\n", "ingest", author=wiki_constants.INGEST_AUTHOR)

    body = client.get(f"/api/wiki/update-health?path={PATH}").json()
    assert body["count_24h"] == 3
    assert body["cap_24h"] == 2
    # Over the cap → a resume time is returned (ISO-8601, ~24h out).
    assert body["cap_resets_at"] is not None
    datetime.fromisoformat(body["cap_resets_at"])


def test_can_manage_true_for_non_admin_owner(client: TestClient) -> None:
    # The page owner (not just admins) can act on the warning, so the banner
    # shows for them too.
    users_repo.create(email="admin@x.com", password="hunter2-x", name="Admin")  # first = admin
    owner = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    wiki_git.commit_file(PATH, "b\n", "ingest", author=wiki_constants.INGEST_AUTHOR)
    acl.set_owner(PATH, owner)
    login_fastapi(client, owner)

    body = client.get(f"/api/wiki/update-health?path={PATH}").json()
    assert body["can_manage"] is True


def test_can_manage_false_for_non_owner_reader(client: TestClient) -> None:
    # The banner + its "Review settings" CTA only render when can_manage is true,
    # so a reader who can't change the policy never sees them.
    owner = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    reader = users_repo.create(email="reader@x.com", password="hunter2-x", name="Read")
    wiki_git.commit_file(PATH, "b\n", "ingest", author=wiki_constants.INGEST_AUTHOR)
    acl.set_owner(PATH, owner)
    acl.grant(
        resource_kind="page",
        resource_path=PATH,
        principal_kind="everyone",
        principal_id=None,
        permission="read",
        granted_by_user_id=owner,
    )
    login_fastapi(client, reader)

    body = client.get(f"/api/wiki/update-health?path={PATH}").json()
    assert body["can_manage"] is False


def test_threshold_falls_back_to_workspace_default(client: TestClient) -> None:
    uid = users_repo.create(email="o@x.com", password="hunter2-x", name="O")
    login_fastapi(client, uid)
    # No per-page row → threshold is the workspace default.
    body = client.get(f"/api/wiki/update-health?path={PATH}").json()
    assert body["threshold_24h"] == ingest_settings.DEFAULT_WARN_UPDATE_THRESHOLD
