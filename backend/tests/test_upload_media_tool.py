"""Tests for the `upload_media` agent/MCP tool.

Guards the contract an agent depends on: bytes in, embeddable markdown out,
anchored to a page the agent's user may write, and exposed over MCP.
"""

from __future__ import annotations

import base64
from typing import Any

from app.auth import User, set_current_user
from app.llm.agents.tools import dispatch as registry_dispatch
from app.mcp_server.tools import MCP_ALLOWED_TOOLS, list_for_mcp
from app.wiki import acl, media_store
from app.wiki import git as wiki_git
from tests._seed import seed_user

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16


def _upload(**args: Any) -> dict[str, Any]:
    return registry_dispatch("upload_media", args)


def _as_user(uid: str = "u1", email: str = "a@x.com"):
    return set_current_user(User(id=uid, email=email, name=None, is_admin=False))


def test_upload_returns_embeddable_markdown(tmp_repo):
    wiki_git.commit_file("guides/setup.md", "# Setup\n", "seed", author=None)
    uid = seed_user(uid="u1", email="a@x.com")

    with _as_user(uid):
        out = _upload(
            path="guides/setup.md",
            data_base64=base64.b64encode(PNG_BYTES).decode(),
            alt_text="a diagram",
        )

    assert "error" not in out
    assert out["markdown"] == f"![a diagram]({out['url']})"
    stored = media_store.stat(out["url"].rsplit("/", 1)[-1])
    assert stored is not None and stored.content_type == "image/png"


def test_upload_types_from_bytes_not_the_alt_text(tmp_repo):
    # The agent never declares a content type, so the sniff is the only source.
    wiki_git.commit_file("guides/setup.md", "# Setup\n", "seed", author=None)
    uid = seed_user(uid="u1", email="a@x.com")

    with _as_user(uid):
        out = _upload(
            path="guides/setup.md",
            data_base64=base64.b64encode(JPEG_BYTES).decode(),
            alt_text="shot.png",
        )

    stored = media_store.stat(out["url"].rsplit("/", 1)[-1])
    assert stored is not None and stored.content_type == "image/jpeg"


def test_upload_rejects_a_page_the_user_cannot_write(tmp_repo):
    wiki_git.commit_file("locked/page.md", "# Locked\n", "seed", author=None)
    owner = seed_user(uid="owner", email="o@x.com")
    acl.set_owner("locked/page.md", owner)
    outsider = seed_user(uid="outsider", email="x@x.com")

    with _as_user(outsider, "x@x.com"):
        out = _upload(
            path="locked/page.md",
            data_base64=base64.b64encode(PNG_BYTES).decode(),
        )

    assert "error" in out
    assert media_store.totals()[0] == 0  # nothing stored


def test_upload_rejects_a_missing_anchor_page(tmp_repo):
    uid = seed_user(uid="u1", email="a@x.com")

    with _as_user(uid):
        out = _upload(
            path="guides/nope.md",
            data_base64=base64.b64encode(PNG_BYTES).decode(),
        )

    assert out["error"] == "anchor page not found"
    assert media_store.totals()[0] == 0


def test_upload_rejects_a_folder_anchor(tmp_repo):
    uid = seed_user(uid="u1", email="a@x.com")

    with _as_user(uid):
        out = _upload(
            path="guides",
            data_base64=base64.b64encode(PNG_BYTES).decode(),
        )

    assert out["error"] == "anchor must be a wiki page"


def test_upload_rejects_non_image_and_bad_base64(tmp_repo):
    wiki_git.commit_file("guides/setup.md", "# Setup\n", "seed", author=None)
    uid = seed_user(uid="u1", email="a@x.com")

    with _as_user(uid):
        not_an_image = _upload(
            path="guides/setup.md",
            data_base64=base64.b64encode(b"just text, not an image").decode(),
        )
        bad_encoding = _upload(path="guides/setup.md", data_base64="not base64!!")

    assert not_an_image["error"] == "unsupported media type"
    assert "not valid base64" in bad_encoding["error"]
    assert media_store.totals()[0] == 0


def test_tool_is_exposed_over_mcp():
    assert "upload_media" in MCP_ALLOWED_TOOLS
    listed = {t["name"]: t for t in list_for_mcp()}
    assert "upload_media" in listed
    schema = listed["upload_media"]["inputSchema"]
    assert set(schema["required"]) == {"path", "data_base64"}
