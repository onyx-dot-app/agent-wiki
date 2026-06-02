"""Tests for ``app/api/comments.py`` — the HTTP layer for wiki comments.

Uses a real wiki repo (``tmp_repo``) so anchors reference real commit SHAs and
the GET re-anchor backstop has a repo to read. Pages committed directly via
``wiki_git`` are unmanaged, so the ACL resolver treats them as implicit-public
(read granted) — enough to exercise the comment routes.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.wiki import git as wiki_git
from tests._auth import login_fastapi
from tests._seed import list_events, seed_user

_PATH = "guides/setup.md"
_BODY = "Intro line here.\nThe target sentence to comment on.\nClosing line.\n"


@pytest.fixture
def client(tmp_repo):
    return TestClient(create_app())


def _commit_page(body: str = _BODY, path: str = _PATH) -> str:
    return wiki_git.commit_file(path, body, "seed", author=None)


def _create(client: TestClient, sha: str, phrase: str = "target sentence", *, path: str = _PATH):
    start = _BODY.index(phrase)
    return client.post(
        "/api/comments",
        json={
            "path": path,
            "anchor_sha": sha,
            "start_offset": start,
            "end_offset": start + len(phrase),
            "quoted_text": phrase,
            "body": "is this still accurate?",
        },
    )


def test_unauthenticated_is_401(client):
    assert client.get(f"/api/comments?path={_PATH}").status_code == 401


def test_create_then_list_as_thread(client):
    uid = seed_user(email="a@x.com")
    login_fastapi(client, uid)
    sha = _commit_page()

    r = _create(client, sha)
    assert r.status_code == 201
    created = r.json()
    assert created["thread_root_id"] == created["id"]
    assert created["scope"] == "inline"
    assert created["status"] == "open"
    assert created["author_user_id"] == uid

    listing = client.get(f"/api/comments?path={_PATH}").json()
    assert len(listing["threads"]) == 1
    thread = listing["threads"][0]
    assert thread["root"]["id"] == created["id"]
    assert thread["replies"] == []


def test_create_fires_page_comment_event(client):
    uid = seed_user(email="a@x.com")
    login_fastapi(client, uid)
    sha = _commit_page()
    _create(client, sha)
    events = list_events("page.comment")
    assert len(events) == 1
    assert events[0]["target"] == _PATH


def test_reply_shows_under_root(client):
    uid = seed_user(email="a@x.com")
    login_fastapi(client, uid)
    sha = _commit_page()
    root = _create(client, sha).json()

    r = client.post(f"/api/comments/{root['id']}/replies", json={"body": "yep"})
    assert r.status_code == 201
    reply = r.json()
    assert reply["parent_id"] == root["id"]
    assert reply["thread_root_id"] == root["id"]
    assert reply["anchor_sha"] is None  # replies inherit position, no anchor

    thread = client.get(f"/api/comments?path={_PATH}").json()["threads"][0]
    assert [c["id"] for c in thread["replies"]] == [reply["id"]]


def test_resolve_and_reopen(client):
    uid = seed_user(email="a@x.com")
    login_fastapi(client, uid)
    sha = _commit_page()
    root = _create(client, sha).json()

    resolved = client.post(f"/api/comments/{root['id']}/resolve").json()
    assert resolved["status"] == "resolved"
    assert resolved["resolved_by_user_id"] == uid

    reopened = client.post(f"/api/comments/{root['id']}/reopen").json()
    assert reopened["status"] == "open"
    assert reopened["resolved_by_user_id"] is None


def test_edit_is_author_only(client):
    author = seed_user(uid="u_author", email="author@x.com")
    other = seed_user(uid="u_other", email="other@x.com")
    login_fastapi(client, author)
    sha = _commit_page()
    root = _create(client, sha).json()

    # Author can edit.
    login_fastapi(client, author)
    ok = client.patch(f"/api/comments/{root['id']}", json={"body": "edited"})
    assert ok.status_code == 200
    assert ok.json()["body"] == "edited"

    # A different user cannot.
    login_fastapi(client, other)
    forbidden = client.patch(f"/api/comments/{root['id']}", json={"body": "nope"})
    assert forbidden.status_code == 403


def test_delete_is_author_only_and_cascades(client):
    author = seed_user(uid="u_author", email="author@x.com")
    other = seed_user(uid="u_other", email="other@x.com")
    login_fastapi(client, author)
    sha = _commit_page()
    root = _create(client, sha).json()
    client.post(f"/api/comments/{root['id']}/replies", json={"body": "r"})

    # Non-author blocked.
    login_fastapi(client, other)
    assert client.delete(f"/api/comments/{root['id']}").status_code == 403

    # Author deletes root -> thread (incl. reply) gone.
    login_fastapi(client, author)
    assert client.delete(f"/api/comments/{root['id']}").status_code == 204
    assert client.get(f"/api/comments?path={_PATH}").json()["threads"] == []


def test_create_rejects_inverted_range(client):
    uid = seed_user(email="a@x.com")
    login_fastapi(client, uid)
    sha = _commit_page()
    r = client.post(
        "/api/comments",
        json={
            "path": _PATH,
            "anchor_sha": sha,
            "start_offset": 10,
            "end_offset": 5,
            "quoted_text": "x",
            "body": "bad range",
        },
    )
    assert r.status_code == 400


def test_actions_on_missing_comment_404(client):
    uid = seed_user(email="a@x.com")
    login_fastapi(client, uid)
    assert client.post("/api/comments/cmt_missing/replies", json={"body": "x"}).status_code == 404
    assert client.patch("/api/comments/cmt_missing", json={"body": "x"}).status_code == 404
    assert client.post("/api/comments/cmt_missing/resolve").status_code == 404
    assert client.delete("/api/comments/cmt_missing").status_code == 404
