"""Tests for the human-edit draft feature.

Two layers are covered:

1. ``app.wiki.git`` — git-plumbing draft functions (save, get, delete,
   delete_for_path).  These use ``tmp_repo`` which gives a real git repo
   under a temp dir so there is nothing to mock.

2. HTTP endpoints (``/api/wiki/file/autosave``, conflict on
   ``PUT /api/wiki/file``) via FastAPI's TestClient.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import users as users_repo
from app.main import create_app
from app.wiki import git as wiki_git

from tests._auth import login_fastapi


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(tmp_db, tmp_repo):
    return TestClient(create_app())


@pytest.fixture
def user(tmp_db):
    return users_repo.create(email="alice@x.com", password="hunter2-x", name="Alice")


@pytest.fixture
def seeded_page(tmp_repo):
    """A committed wiki page; returns its initial head SHA."""
    sha = wiki_git.commit_file("notes.md", "# Notes\n\nOriginal.\n", "initial", author=None)
    return sha


# --------------------------------------------------------------------------- #
# git.save_draft / get_draft                                                   #
# --------------------------------------------------------------------------- #


def test_get_draft_returns_none_when_no_draft(tmp_repo, seeded_page):
    assert wiki_git.get_draft("notes.md", "user-1") is None


def test_save_draft_then_get_draft_roundtrip(tmp_repo, seeded_page):
    wiki_git.save_draft("notes.md", "user-1", "my draft\n", seeded_page)

    draft = wiki_git.get_draft("notes.md", "user-1")
    assert draft is not None
    assert draft["path"] == "notes.md"
    assert draft["content"] == "my draft\n"
    assert draft["base_sha"] == seeded_page
    assert draft["updated_at"]  # non-empty ISO timestamp


def test_save_draft_overwrites_previous_content(tmp_repo, seeded_page):
    wiki_git.save_draft("notes.md", "user-1", "first\n", seeded_page)
    wiki_git.save_draft("notes.md", "user-1", "second\n", seeded_page)

    draft = wiki_git.get_draft("notes.md", "user-1")
    assert draft is not None
    assert draft["content"] == "second\n"


def test_drafts_are_isolated_per_user(tmp_repo, seeded_page):
    wiki_git.save_draft("notes.md", "user-a", "alice's draft\n", seeded_page)
    wiki_git.save_draft("notes.md", "user-b", "bob's draft\n", seeded_page)

    assert wiki_git.get_draft("notes.md", "user-a")["content"] == "alice's draft\n"  # type: ignore[index]
    assert wiki_git.get_draft("notes.md", "user-b")["content"] == "bob's draft\n"  # type: ignore[index]


def test_draft_path_with_spaces(tmp_repo):
    """Spaces in the file path must be percent-encoded in the ref name."""
    sha = wiki_git.commit_file("oncall runbook.md", "body\n", "create", author=None)
    wiki_git.save_draft("oncall runbook.md", "user-1", "draft\n", sha)

    draft = wiki_git.get_draft("oncall runbook.md", "user-1")
    assert draft is not None
    assert draft["content"] == "draft\n"


# --------------------------------------------------------------------------- #
# git.delete_draft                                                             #
# --------------------------------------------------------------------------- #


def test_delete_draft_removes_draft(tmp_repo, seeded_page):
    wiki_git.save_draft("notes.md", "user-1", "draft\n", seeded_page)
    wiki_git.delete_draft("notes.md", "user-1")

    assert wiki_git.get_draft("notes.md", "user-1") is None


def test_delete_draft_is_idempotent_when_no_draft(tmp_repo, seeded_page):
    # Should not raise even when there is nothing to delete.
    wiki_git.delete_draft("notes.md", "user-missing")


# --------------------------------------------------------------------------- #
# git.delete_drafts_for_path                                                  #
# --------------------------------------------------------------------------- #


def test_delete_drafts_for_path_removes_all_user_drafts(tmp_repo, seeded_page):
    wiki_git.save_draft("notes.md", "user-a", "a\n", seeded_page)
    wiki_git.save_draft("notes.md", "user-b", "b\n", seeded_page)

    wiki_git.delete_drafts_for_path("notes.md")

    assert wiki_git.get_draft("notes.md", "user-a") is None
    assert wiki_git.get_draft("notes.md", "user-b") is None


def test_delete_drafts_for_path_does_not_touch_other_paths(tmp_repo):
    sha_a = wiki_git.commit_file("a.md", "a\n", "create a", author=None)
    sha_b = wiki_git.commit_file("b.md", "b\n", "create b", author=None)

    wiki_git.save_draft("a.md", "user-1", "da\n", sha_a)
    wiki_git.save_draft("b.md", "user-1", "db\n", sha_b)

    wiki_git.delete_drafts_for_path("a.md")

    assert wiki_git.get_draft("a.md", "user-1") is None
    assert wiki_git.get_draft("b.md", "user-1") is not None


# --------------------------------------------------------------------------- #
# GET /api/wiki/file/autosave                                                 #
# --------------------------------------------------------------------------- #


def test_autosave_get_returns_401_when_unauthenticated(client):
    assert client.get("/api/wiki/file/autosave?path=notes.md").status_code == 401


def test_autosave_get_returns_null_when_no_draft(client, user, seeded_page):
    login_fastapi(client, user)
    resp = client.get("/api/wiki/file/autosave?path=notes.md")
    assert resp.status_code == 200
    assert resp.json() is None


def test_autosave_get_returns_draft_after_save(client, user, seeded_page):
    login_fastapi(client, user)

    put_resp = client.put(
        "/api/wiki/file/autosave",
        json={"path": "notes.md", "base_sha": seeded_page, "content": "my draft\n"},
    )
    assert put_resp.status_code == 200

    get_resp = client.get("/api/wiki/file/autosave?path=notes.md")
    assert get_resp.status_code == 200
    payload = get_resp.json()
    assert payload["path"] == "notes.md"
    assert payload["content"] == "my draft\n"
    assert payload["base_sha"] == seeded_page


# --------------------------------------------------------------------------- #
# PUT /api/wiki/file/autosave                                                 #
# --------------------------------------------------------------------------- #


def test_autosave_put_returns_401_when_unauthenticated(client, seeded_page):
    resp = client.put(
        "/api/wiki/file/autosave",
        json={"path": "notes.md", "base_sha": seeded_page, "content": "x"},
    )
    assert resp.status_code == 401


def test_autosave_put_creates_draft(client, user, seeded_page):
    login_fastapi(client, user)
    resp = client.put(
        "/api/wiki/file/autosave",
        json={"path": "notes.md", "base_sha": seeded_page, "content": "draft body\n"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == "notes.md"
    assert body["content"] == "draft body\n"


# --------------------------------------------------------------------------- #
# DELETE /api/wiki/file/autosave                                              #
# --------------------------------------------------------------------------- #


def test_autosave_delete_removes_draft(client, user, seeded_page):
    login_fastapi(client, user)

    client.put(
        "/api/wiki/file/autosave",
        json={"path": "notes.md", "base_sha": seeded_page, "content": "to discard\n"},
    )

    del_resp = client.delete("/api/wiki/file/autosave?path=notes.md")
    assert del_resp.status_code == 204

    get_resp = client.get("/api/wiki/file/autosave?path=notes.md")
    assert get_resp.json() is None


def test_autosave_delete_is_idempotent(client, user):
    login_fastapi(client, user)
    resp = client.delete("/api/wiki/file/autosave?path=ghost.md")
    assert resp.status_code == 204


# --------------------------------------------------------------------------- #
# PUT /api/wiki/file — 409 on stale base_sha                                  #
# --------------------------------------------------------------------------- #


def test_put_file_succeeds_with_matching_base_sha(client, user, seeded_page):
    login_fastapi(client, user)
    resp = client.put(
        "/api/wiki/file",
        json={"path": "notes.md", "body": "updated\n", "base_sha": seeded_page},
    )
    assert resp.status_code == 200


def test_put_file_returns_409_when_base_sha_is_stale(client, user, seeded_page):
    login_fastapi(client, user)

    # Advance HEAD past seeded_page by committing another version.
    wiki_git.commit_file("notes.md", "concurrent edit\n", "other user", author=None)

    # Now try to save with the old SHA — should conflict.
    resp = client.put(
        "/api/wiki/file",
        json={"path": "notes.md", "body": "my edit\n", "base_sha": seeded_page},
    )
    assert resp.status_code == 409
    assert resp.json()["error"] == "conflict detected"


def test_put_file_deletes_draft_on_successful_save(client, user, seeded_page):
    login_fastapi(client, user)

    # Save a draft first.
    client.put(
        "/api/wiki/file/autosave",
        json={"path": "notes.md", "base_sha": seeded_page, "content": "draft\n"},
    )

    # Successful commit should clean up the draft.
    client.put(
        "/api/wiki/file",
        json={"path": "notes.md", "body": "draft\n", "base_sha": seeded_page},
    )

    get_resp = client.get("/api/wiki/file/autosave?path=notes.md")
    assert get_resp.json() is None


# --------------------------------------------------------------------------- #
# POST /api/wiki/file/autosave/rebase                                         #
# --------------------------------------------------------------------------- #


def test_rebase_returns_404_when_no_draft(client, user, seeded_page):
    login_fastapi(client, user)
    resp = client.post(
        "/api/wiki/file/autosave/rebase",
        json={"path": "notes.md"},
    )
    assert resp.status_code == 404


def test_rebase_returns_404_when_draft_is_current(client, user, seeded_page):
    """No divergence — rebase_draft returns None → 404."""
    login_fastapi(client, user)
    client.put(
        "/api/wiki/file/autosave",
        json={"path": "notes.md", "base_sha": seeded_page, "content": "draft\n"},
    )
    # Draft base_sha == HEAD — nothing to rebase.
    resp = client.post(
        "/api/wiki/file/autosave/rebase",
        json={"path": "notes.md"},
    )
    assert resp.status_code == 404


def test_rebase_clean_merge_returns_200_and_updates_draft(client, user, seeded_page):
    """Changes on different lines → clean 3-way merge, draft rebased onto HEAD."""
    login_fastapi(client, user)

    # Save a draft that adds a line at the bottom.
    client.put(
        "/api/wiki/file/autosave",
        json={
            "path": "notes.md",
            "base_sha": seeded_page,
            "content": "# Notes\n\nOriginal.\n\nMy addition.\n",
        },
    )

    # Advance HEAD with a non-overlapping change at the top.
    new_sha = wiki_git.commit_file(
        "notes.md", "# Notes — Updated\n\nOriginal.\n", "concurrent edit", author=None
    )

    resp = client.post(
        "/api/wiki/file/autosave/rebase",
        json={"path": "notes.md"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["base_sha"] == new_sha
    # Merged result should contain both edits.
    assert "Updated" in body["content"]
    assert "My addition" in body["content"]

    # Draft on server is now rebased onto the new HEAD.
    draft = wiki_git.get_draft("notes.md", user)
    assert draft is not None
    assert draft["base_sha"] == new_sha


def test_rebase_conflict_returns_409_with_conflict_details(client, user, seeded_page):
    """Overlapping edits → conflict markers, 409 with current/draft bodies."""
    login_fastapi(client, user)

    # Draft changes the same line as the concurrent commit.
    client.put(
        "/api/wiki/file/autosave",
        json={
            "path": "notes.md",
            "base_sha": seeded_page,
            "content": "# Notes\n\nDraft version.\n",
        },
    )
    wiki_git.commit_file(
        "notes.md", "# Notes\n\nConcurrent version.\n", "concurrent", author=None
    )

    resp = client.post(
        "/api/wiki/file/autosave/rebase",
        json={"path": "notes.md"},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert "current_body" in body
    assert "draft_body" in body
    assert "current_sha" in body
    assert "Draft version" in body["draft_body"]
    assert "Concurrent version" in body["current_body"]
