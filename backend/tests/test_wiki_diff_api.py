"""Tests for GET /api/wiki/file/diff."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import users as users_repo
from app.main import create_app
from app.wiki import git as wiki_git
from tests._auth import login_fastapi


@pytest.fixture
def client(tmp_db, tmp_repo):
    return TestClient(create_app())


def test_diff_endpoint_returns_structured_diff(client: TestClient) -> None:
    uid = users_repo.create(email="nik@x.com", password="hunter2-x", name="Nik")
    login_fastapi(client, uid)

    rel = "notes/page.md"
    wiki_git.commit_file(rel, "alpha\n", "create", author=None)
    second = wiki_git.commit_file(rel, "beta\n", "edit", author=None)

    resp = client.get(f"/api/wiki/file/diff?path={rel}&sha={second}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == rel
    assert body["sha"] == second
    assert body["is_creation"] is False
    assert body["parent_sha"] is not None
    assert body["hunks"]


def test_diff_endpoint_400_on_missing_args(client: TestClient) -> None:
    uid = users_repo.create(email="nik@x.com", password="hunter2-x", name="Nik")
    login_fastapi(client, uid)
    assert client.get("/api/wiki/file/diff").status_code == 400
    assert client.get("/api/wiki/file/diff?path=notes/page.md").status_code == 400


def test_diff_endpoint_400_on_bad_sha(client: TestClient) -> None:
    uid = users_repo.create(email="nik@x.com", password="hunter2-x", name="Nik")
    login_fastapi(client, uid)
    rel = "notes/page.md"
    wiki_git.commit_file(rel, "x\n", "create", author=None)
    resp = client.get(f"/api/wiki/file/diff?path={rel}&sha=NOTHEX!")
    assert resp.status_code == 400


def test_diff_endpoint_404_when_sha_doesnt_touch_path(client: TestClient) -> None:
    uid = users_repo.create(email="nik@x.com", password="hunter2-x", name="Nik")
    login_fastapi(client, uid)
    wiki_git.commit_file("notes/other.md", "x\n", "create", author=None)
    untouched = wiki_git.commit_file("notes/another.md", "y\n", "create", author=None)
    resp = client.get(f"/api/wiki/file/diff?path=notes/other.md&sha={untouched}")
    assert resp.status_code == 404
