"""Anchor-checked image ingest.

The caller supplies bytes and an anchor page. Everything deciding whether those
bytes may become an image lives here, so every entry point enforces one set of
rules in one order.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.auth import User, require_can
from app.models.images import UploadImageResponse
from app.wiki import doc_ids, filesystem, image_store
from app.wiki import git as wiki_git

UPLOAD_CAP_BYTES = 10 * 1024 * 1024


class ImageUploadError(Exception):
    """A rejected upload. ``reason`` labels the metrics counter, ``status`` is
    the HTTP status the route maps to."""

    def __init__(self, message: str, *, status: int, reason: str | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.reason = reason


class AnchorPath(BaseModel):
    """A validated anchor page path."""

    rel: str


def validate_anchor(path: str) -> AnchorPath:
    """Reject anything that is not a wiki page before any bytes are read."""
    if not path.strip():
        raise ImageUploadError("path required", status=400)
    try:
        rel = filesystem.safe_rel_path(path)
    except ValueError as exc:
        raise ImageUploadError(str(exc), status=400) from exc
    # Anchors are wiki pages. Folders and tracked non-page files (trigger
    # YAML, .gitkeep) must not accumulate image anchors.
    if not rel.endswith(".md"):
        raise ImageUploadError("anchor must be a wiki page", status=400)
    return AnchorPath(rel=rel)


def store(
    *, data: bytes, anchor: AnchorPath, filename: str | None, user: User
) -> UploadImageResponse:
    """Store ``data`` against ``anchor``, or raise ``ImageUploadError``.

    Write permission is checked on the anchor page, so an agent can never
    attach to a page its user cannot edit.
    """
    require_can("write", anchor.rel, user)
    if not data:
        raise ImageUploadError("empty image body", status=400)
    if len(data) > UPLOAD_CAP_BYTES:
        raise ImageUploadError(
            "image exceeds 10 MiB limit", status=413, reason="too_large"
        )

    # The sniffed type is what gets stored and served, so a declared
    # content-type is never compared. A JPEG named .png would fail for nothing.
    sniffed = image_store.sniff_image_type(data)
    if sniffed is None:
        raise ImageUploadError(
            "unsupported image type", status=415, reason="unsupported_type"
        )

    # Uploading to a page that doesn't exist at HEAD would mint a live doc id
    # for it. Presence check, not history: a deleted or trashed page still has
    # commits touching its old path.
    if not wiki_git.exists_at_head(anchor.rel):
        raise ImageUploadError("anchor page not found", status=404)
    anchor_doc_id = doc_ids.get_or_mint(anchor.rel)
    image_id = image_store.put(
        data=data,
        content_type=sniffed,
        anchor_doc_id=anchor_doc_id,
        uploaded_by=user.id,
    )
    # Brackets the mint+put against a concurrent page move/trash/delete: if
    # the page left HEAD in the window, drop the blob instead of orphaning it.
    if not wiki_git.exists_at_head(anchor.rel):
        image_store.delete(image_id)
        raise ImageUploadError("anchor page not found", status=404)

    url = image_store.serving_url(image_id)
    alt = (filename or "").replace("\n", " ").replace("\r", " ").replace("]", "")
    return UploadImageResponse(id=image_id, url=url, markdown=f"![{alt}]({url})")
