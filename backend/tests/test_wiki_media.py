"""Tests for wiki image upload, storage, auth, and conditional serving."""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from app.auth import users as users_repo
from app.main import create_app
from app.db.models import Media
from app.wiki import acl, doc_ids, media_store
from app.wiki import git as wiki_git
from tests._seed import count_rows
from app.wiki.media_store import sniff_media_type
from tests._auth import login_fastapi

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16
GIF_BYTES = b"GIF89a" + b"\x00" * 16
WEBP_BYTES = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 8


@pytest.fixture
def client(tmp_repo):
    # Upload anchors must be real pages, so seed the ones the tests target.
    wiki_git.commit_file("guides/setup.md", "# Setup\n", "seed")
    wiki_git.commit_file("priv/secret.md", "# Secret\n", "seed")
    return TestClient(create_app())


def test_upload_400_for_folder_and_non_page_anchors(client: TestClient) -> None:
    # A directory or a tracked non-page file is a git object but not a page.
    uid = users_repo.create(email="admin@x.com", password="hunter2-x", name="Admin")
    login_fastapi(client, uid)
    wiki_git.commit_file("guides/.gitkeep", "", "seed")
    for bad in ("guides", "guides/.gitkeep"):
        resp = client.post(
            f"/api/wiki/media?path={bad}",
            content=PNG_BYTES,
            headers={"content-type": "image/png"},
        )
        assert resp.status_code == 400
    assert count_rows(Media) == 0


def test_upload_404_for_formerly_existing_deleted_page(client: TestClient) -> None:
    # A deleted page still has commits touching its path, so the guard must
    # test HEAD presence, not history.
    uid = users_repo.create(email="admin@x.com", password="hunter2-x", name="Admin")
    login_fastapi(client, uid)
    wiki_git.commit_file("guides/gone.md", "# Gone\n", "seed")
    wiki_git.delete_path("guides/gone.md", "remove")
    resp = client.post(
        "/api/wiki/media?path=guides/gone.md",
        content=PNG_BYTES,
        headers={"content-type": "image/png"},
    )
    assert resp.status_code == 404
    assert count_rows(Media) == 0


def test_upload_cleans_up_when_page_vanishes_mid_upload(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulates the page being trashed between the pre-mint existence check
    # and the post-put re-check: first probe sees the page, second does not.
    uid = users_repo.create(email="admin@x.com", password="hunter2-x", name="Admin")
    login_fastapi(client, uid)
    real = wiki_git.exists_at_head
    calls = {"n": 0}

    def racing(rel_path: str) -> bool:
        calls["n"] += 1
        return False if calls["n"] > 1 else real(rel_path)

    monkeypatch.setattr("app.wiki.media_upload.wiki_git.exists_at_head", racing)
    resp = client.post(
        "/api/wiki/media?path=guides/setup.md",
        content=PNG_BYTES,
        headers={"content-type": "image/png"},
    )
    assert resp.status_code == 404
    assert count_rows(Media) == 0


def test_upload_404_for_nonexistent_anchor_page(client: TestClient) -> None:
    uid = users_repo.create(email="admin@x.com", password="hunter2-x", name="Admin")
    login_fastapi(client, uid)
    resp = client.post(
        "/api/wiki/media?path=nope/missing.md",
        content=PNG_BYTES,
        headers={"content-type": "image/png"},
    )
    assert resp.status_code == 404
    # No doc id row may be minted for the nonexistent page.
    assert doc_ids.id_for_path("nope/missing.md") is None


def _make_private(path: str, owner_uid: str) -> None:
    acl.set_owner(path, owner_uid)
    for grant in acl.list_for_path(path):
        if grant["principal_kind"] == "everyone":
            acl.revoke(grant["id"])


def test_sniff_media_type_detects_supported_magic_bytes() -> None:
    assert sniff_media_type(PNG_BYTES) == "image/png"
    assert sniff_media_type(JPEG_BYTES) == "image/jpeg"
    assert sniff_media_type(GIF_BYTES) == "image/gif"
    assert sniff_media_type(WEBP_BYTES) == "image/webp"


def test_sniff_media_type_rejects_unknown_and_too_short() -> None:
    assert sniff_media_type(b"not an image") is None
    assert sniff_media_type(b"RIFF") is None


def test_upload_ignores_a_mislabelled_content_type(client: TestClient) -> None:
    # Browsers type a File from its extension, so a JPEG saved as .png arrives
    # declared as image/png. The bytes decide, and the sniffed type is served.
    uid = users_repo.create(email="admin@x.com", password="hunter2-x", name="Admin")
    login_fastapi(client, uid)

    resp = client.post(
        "/api/wiki/media?path=guides/setup.md",
        content=PNG_BYTES,
        headers={"Content-Type": "image/jpeg"},
    )

    assert resp.status_code == 200
    served = client.get(resp.json()["url"])
    assert served.headers["Content-Type"] == "image/png"


def test_upload_without_a_content_type_still_sniffs(client: TestClient) -> None:
    uid = users_repo.create(email="admin@x.com", password="hunter2-x", name="Admin")
    login_fastapi(client, uid)

    resp = client.post("/api/wiki/media?path=guides/setup.md", content=PNG_BYTES)

    assert resp.status_code == 200
    served = client.get(resp.json()["url"])
    assert served.headers["Content-Type"] == "image/png"


def test_upload_rejects_oversize_body(client: TestClient) -> None:
    uid = users_repo.create(email="admin@x.com", password="hunter2-x", name="Admin")
    login_fastapi(client, uid)

    resp = client.post(
        "/api/wiki/media?path=guides/setup.md",
        content=b"\x89PNG\r\n\x1a\n" + b"\x00" * (10 * 1024 * 1024 + 1),
        headers={"Content-Type": "image/png"},
    )

    assert resp.status_code == 413
    assert resp.json() == {"error": "file exceeds 10 MiB limit"}


def test_media_store_round_trip(tmp_db) -> None:
    uid = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")

    image_id = media_store.put(
        data=PNG_BYTES,
        content_type="image/png",
        anchor_doc_id="doc-1",
        uploaded_by=uid,
    )

    rec = media_store.get(image_id)
    assert rec is not None
    assert rec.data == PNG_BYTES
    assert rec.sha256 == hashlib.sha256(PNG_BYTES).hexdigest()
    assert rec.size_bytes == len(PNG_BYTES)
    assert rec.anchor_doc_id == "doc-1"
    assert rec.uploaded_by == uid

    meta = media_store.stat(image_id)
    assert meta is not None
    assert meta.id == image_id
    assert meta.sha256 == rec.sha256
    assert meta.size_bytes == len(PNG_BYTES)
    assert not hasattr(meta, "data")

    assert media_store.delete(image_id) is True
    assert media_store.get(image_id) is None


def test_upload_and_serve_round_trip(client: TestClient) -> None:
    uid = users_repo.create(email="admin@x.com", password="hunter2-x", name="Admin")
    login_fastapi(client, uid)

    upload = client.post(
        "/api/wiki/media?path=guides/setup.md&filename=logo.png",
        content=PNG_BYTES,
        headers={"Content-Type": "image/png"},
    )

    assert upload.status_code == 200
    payload = upload.json()
    assert payload["id"]
    assert payload["url"] == f"/api/wiki/media/{payload['id']}"
    assert payload["markdown"] == f"![logo.png]({payload['url']})"

    served = client.get(payload["url"])

    assert served.status_code == 200
    assert served.content == PNG_BYTES
    assert served.headers["Content-Type"] == "image/png"
    assert served.headers["ETag"]
    assert served.headers["Cache-Control"] == "private, no-cache"
    assert served.headers["X-Content-Type-Options"] == "nosniff"


def test_upload_is_403_without_write_permission(client: TestClient) -> None:
    owner_uid = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    denied_uid = users_repo.create(email="denied@x.com", password="hunter2-x", name="Denied")
    _make_private("priv/secret.md", owner_uid)
    login_fastapi(client, denied_uid)

    resp = client.post(
        "/api/wiki/media?path=priv/secret.md",
        content=PNG_BYTES,
        headers={"Content-Type": "image/png"},
    )

    assert resp.status_code == 403


def test_serve_is_403_without_read_permission(client: TestClient) -> None:
    owner_uid = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    denied_uid = users_repo.create(email="denied@x.com", password="hunter2-x", name="Denied")
    _make_private("priv/secret.md", owner_uid)
    login_fastapi(client, owner_uid)

    upload = client.post(
        "/api/wiki/media?path=priv/secret.md",
        content=PNG_BYTES,
        headers={"Content-Type": "image/png"},
    )
    assert upload.status_code == 200
    image_url = upload.json()["url"]

    login_fastapi(client, denied_uid)
    resp = client.get(image_url)

    assert resp.status_code == 403


def test_serve_unknown_image_is_404(client: TestClient) -> None:
    uid = users_repo.create(email="admin@x.com", password="hunter2-x", name="Admin")
    login_fastapi(client, uid)

    resp = client.get("/api/wiki/media/doesnotexist")

    assert resp.status_code == 404
    assert resp.json() == {"error": "not found"}


def test_serve_tombstoned_anchor_is_404(client: TestClient) -> None:
    uid = users_repo.create(email="admin@x.com", password="hunter2-x", name="Admin")
    login_fastapi(client, uid)

    upload = client.post(
        "/api/wiki/media?path=guides/setup.md",
        content=PNG_BYTES,
        headers={"Content-Type": "image/png"},
    )
    assert upload.status_code == 200

    rel = "guides/setup.md"
    doc_ids.on_deleted(rel)
    resp = client.get(upload.json()["url"])

    assert resp.status_code == 404
    assert resp.json() == {"error": "not found"}


def test_serve_returns_304_for_matching_etag(client: TestClient) -> None:
    uid = users_repo.create(email="admin@x.com", password="hunter2-x", name="Admin")
    login_fastapi(client, uid)

    upload = client.post(
        "/api/wiki/media?path=guides/setup.md",
        content=PNG_BYTES,
        headers={"Content-Type": "image/png"},
    )
    assert upload.status_code == 200
    image_url = upload.json()["url"]

    first = client.get(image_url)
    assert first.status_code == 200
    etag = first.headers["ETag"]

    second = client.get(image_url, headers={"If-None-Match": etag})

    assert second.status_code == 304
    assert second.content == b""
    assert second.headers["ETag"] == etag
    assert second.headers["Cache-Control"] == "private, no-cache"


def test_unauthenticated_upload_is_401(client: TestClient) -> None:
    resp = client.post(
        "/api/wiki/media?path=guides/setup.md",
        content=PNG_BYTES,
        headers={"Content-Type": "image/png"},
    )

    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


def test_unauthenticated_serve_is_401(client: TestClient) -> None:
    owner_uid = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    login_fastapi(client, owner_uid)
    upload = client.post(
        "/api/wiki/media?path=guides/setup.md",
        content=PNG_BYTES,
        headers={"Content-Type": "image/png"},
    )
    assert upload.status_code == 200
    image_url = upload.json()["url"]
    client.cookies.delete("session")

    resp = client.get(image_url)

    assert resp.status_code == 401
    assert resp.json() == {"error": "missing bearer token"}
