"""Tests for GET /api/wiki/update-health and the admin /api/admin/app-settings."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.app_settings import settings as app_settings
from app.auth import users as users_repo
from app.main import create_app
from app.wiki import acl
from app.wiki import git as wiki_git
from app.wiki import update_policy
from app.wiki import utils as wiki_utils
from tests._auth import login_fastapi

PATH = "team/page.md"


@pytest.fixture
def client(tmp_db: None, tmp_repo: None) -> TestClient:
    return TestClient(create_app())


def test_update_health_unauthenticated_is_401(client: TestClient) -> None:
    assert client.get(f"/api/wiki/update-health?path={PATH}").status_code == 401


def test_update_health_flags_owner_banner_over_threshold(client: TestClient) -> None:
    uid = users_repo.create(email="o@x.com", password="hunter2-x", name="O")
    login_fastapi(client, uid)
    acl.set_owner(PATH, uid)
    for i in range(3):
        wiki_git.commit_file(PATH, f"b{i}\n", "ingest", author=wiki_utils.INGEST_AUTHOR)
    update_policy.set_policy(PATH, warn_update_threshold=2)

    body = client.get(f"/api/wiki/update-health?path={PATH}").json()
    assert body["count"] == 3
    assert body["threshold"] == 2
    assert body["over_threshold"] is True
    assert body["show_banner"] is True


def test_update_health_non_owner_no_banner(client: TestClient) -> None:
    owner = users_repo.create(email="o@x.com", password="hunter2-x", name="O")
    viewer = users_repo.create(email="v@x.com", password="hunter2-x", name="V")
    acl.set_owner(PATH, owner)
    # Owner-stamped pages are private; let everyone read so a non-owner viewer
    # can load the page (and confirm they still get no banner).
    acl.grant(
        resource_kind="page",
        resource_path=PATH,
        principal_kind="everyone",
        principal_id=None,
        permission="read",
        granted_by_user_id=owner,
    )
    for i in range(3):
        wiki_git.commit_file(PATH, f"b{i}\n", "ingest", author=wiki_utils.INGEST_AUTHOR)
    update_policy.set_policy(PATH, warn_update_threshold=2)

    login_fastapi(client, viewer)
    body = client.get(f"/api/wiki/update-health?path={PATH}").json()
    assert body["over_threshold"] is True
    assert body["show_banner"] is False  # not the owner / admin


def test_admin_app_settings_get_put(client: TestClient) -> None:
    admin = users_repo.create(email="admin@x.com", password="hunter2-x", name="A")
    login_fastapi(client, admin)  # first user is auto-admin

    assert client.get("/api/admin/app-settings").json() == {
        "warn_update_threshold_default": 10,
        "auto_update_cap": 0,
    }
    put = client.put(
        "/api/admin/app-settings",
        json={"warn_update_threshold_default": 5, "auto_update_cap": 25},
    )
    assert put.status_code == 200
    assert put.json()["auto_update_cap"] == 25
    assert app_settings.get().warn_update_threshold_default == 5


def test_admin_app_settings_requires_admin(client: TestClient) -> None:
    users_repo.create(email="admin@x.com", password="hunter2-x", name="A")  # admin #1
    member = users_repo.create(email="m@x.com", password="hunter2-x", name="M")
    login_fastapi(client, member)
    assert client.get("/api/admin/app-settings").status_code == 403
