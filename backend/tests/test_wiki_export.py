"""Markdown export: single-page download, folder/whole-wiki zips, link
rewriting, and ACL filtering (``app.wiki.export`` + ``GET /api/wiki/export``).
"""
from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient

from app.main import create_app
from app.wiki import acl
from app.wiki import git as wiki_git
from app.wiki.export import content_disposition, rewrite_links

from tests._auth import login_fastapi
from tests._seed import seed_user


def _client(user_id: str) -> TestClient:
    client = TestClient(create_app())
    login_fastapi(client, user_id)
    return client


def _zip_names(resp) -> set[str]:
    return set(zipfile.ZipFile(io.BytesIO(resp.content)).namelist())


def _make_private(path: str) -> None:
    """Strip the default-public grants a page got on create; the owner row
    stays, so the page is managed and readable only by owner/admin."""
    for entry in acl.list_for_path(path):
        acl.revoke(entry["id"])


# --------------------------------------------------------------------------- #
# Link rewriting (pure)                                                       #
# --------------------------------------------------------------------------- #


def test_rewrite_absolute_app_links_to_relative():
    body = (
        "[sibling](/app/wiki/docs/sub b.md) and "
        "[cousin](/app/wiki/c.md#sec) and "
        "[encoded](/app/wiki/My%20Page.md)"
    )
    out = rewrite_links(body, "docs/a.md")
    assert "[sibling](sub b.md)" in out
    assert "[cousin](../c.md#sec)" in out
    assert "[encoded](../My Page.md)" in out


def test_rewrite_leaves_external_relative_and_image_links_alone():
    body = (
        "[ext](https://example.com/x) [rel](sub/b.md) [anchor](#top) "
        "![img](/app/wiki/pic.png)"
    )
    assert rewrite_links(body, "docs/a.md") == body


def test_rewrite_from_root_document():
    assert rewrite_links("[b](/app/wiki/docs/b.md)", "a.md") == "[b](docs/b.md)"


def test_content_disposition_non_ascii_filename():
    value = content_disposition("Zusammenfassung — Тест.md")
    assert value.startswith("attachment; ")
    assert 'filename="Zusammenfassung  .md"' in value
    assert "filename*=UTF-8''" in value


# --------------------------------------------------------------------------- #
# Endpoint                                                                    #
# --------------------------------------------------------------------------- #


def test_export_single_page(tmp_repo):
    user = seed_user()
    client = _client(user)
    client.put(
        "/api/wiki/file",
        json={"path": "docs/a.md", "body": "# A\n[b](/app/wiki/docs/b.md)\n"},
    )

    resp = client.get("/api/wiki/export", params={"path": "docs/a.md"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert 'filename="a.md"' in resp.headers["content-disposition"]
    assert resp.text == "# A\n[b](b.md)\n"


def test_export_folder_zip_scopes_to_prefix(tmp_repo):
    user = seed_user()
    client = _client(user)
    client.put("/api/wiki/file", json={"path": "team/x.md", "body": "# X\n"})
    client.put("/api/wiki/file", json={"path": "team/sub/y.md", "body": "# Y\n"})
    client.put("/api/wiki/file", json={"path": "other/z.md", "body": "# Z\n"})

    resp = client.get("/api/wiki/export", params={"path": "team"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert 'filename="team-export.zip"' in resp.headers["content-disposition"]
    assert _zip_names(resp) == {"team/x.md", "team/sub/y.md"}


def test_export_whole_wiki_zip(tmp_repo):
    user = seed_user()
    client = _client(user)
    client.put("/api/wiki/file", json={"path": "a.md", "body": "# A\n"})
    client.put("/api/wiki/file", json={"path": "deep/b.md", "body": "# B\n"})

    resp = client.get("/api/wiki/export")
    assert resp.status_code == 200
    assert 'filename="wiki-export.zip"' in resp.headers["content-disposition"]
    assert {"a.md", "deep/b.md"} <= _zip_names(resp)


def test_export_zip_excludes_dot_metadata_pages(tmp_repo):
    user = seed_user()
    client = _client(user)
    client.put("/api/wiki/file", json={"path": "team/x.md", "body": "# X\n"})
    # Dot paths bypass the page-write API; commit them like app metadata does.
    wiki_git.commit_file("team/.metadata.md", "meta\n", "seed metadata")
    wiki_git.commit_file("team/.meta/state.md", "state\n", "seed state")

    resp = client.get("/api/wiki/export", params={"path": "team"})
    assert _zip_names(resp) == {"team/x.md"}


def test_export_zip_drops_unreadable_pages(tmp_repo):
    owner = seed_user(uid="u_owner", email="owner@x.com")
    other = seed_user(uid="u_other", email="other@x.com")
    owner_client = _client(owner)
    owner_client.put("/api/wiki/file", json={"path": "team/open.md", "body": "# O\n"})
    owner_client.put(
        "/api/wiki/file", json={"path": "team/secret.md", "body": "# S\n"}
    )
    _make_private("team/secret.md")

    names = _zip_names(_client(other).get("/api/wiki/export", params={"path": "team"}))
    assert names == {"team/open.md"}
    owner_names = _zip_names(
        owner_client.get("/api/wiki/export", params={"path": "team"})
    )
    assert owner_names == {"team/open.md", "team/secret.md"}


def test_export_single_page_forbidden(tmp_repo):
    owner = seed_user(uid="u_owner", email="owner@x.com")
    other = seed_user(uid="u_other", email="other@x.com")
    _client(owner).put(
        "/api/wiki/file", json={"path": "team/secret.md", "body": "# S\n"}
    )
    _make_private("team/secret.md")

    resp = _client(other).get("/api/wiki/export", params={"path": "team/secret.md"})
    assert resp.status_code == 403


def test_export_rejects_traversal_and_missing(tmp_repo):
    user = seed_user()
    client = _client(user)
    assert (
        client.get("/api/wiki/export", params={"path": "../etc"}).status_code == 400
    )
    assert (
        client.get("/api/wiki/export", params={"path": "nope/void"}).status_code == 404
    )
    assert (
        client.get("/api/wiki/export", params={"path": "nope/void.md"}).status_code
        == 404
    )
