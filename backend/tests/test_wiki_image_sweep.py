"""Retention sweep tests for wiki images."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.auth import users as users_repo
from app.db.models import Image
from app.db.session import session
from app.main import create_app
from app.metrics import (
    wiki_image_sweep_deleted_total,
    wiki_image_upload_rejected_total,
    wiki_images_bytes_total,
    wiki_images_total,
)
from app.tasks.image_sweep import sweep_wiki_images
from app.tasks.queues import lightweight_maintenance_queue
from app.tasks.trash_purge import TRASH_RETENTION_DAYS
from app.wiki import coedit, doc_ids, git as wiki_git, image_store, trash

from tests._auth import login_fastapi

_TS_FORMAT = "%Y-%m-%d %H:%M:%S"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _timestamp_ago(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) - delta).strftime(_TS_FORMAT)


def _put_image(path: str) -> str:
    return image_store.put(
        data=PNG_BYTES,
        content_type="image/png",
        anchor_doc_id=doc_ids.get_or_mint(path),
        uploaded_by=None,
    )


def _body_with_image(image_id: str) -> str:
    return f"# Page\n![x](/api/wiki/images/{image_id})\n"


def _image_row(image_id: str) -> image_store.ImageSweepRow | None:
    for row in image_store.list_for_sweep():
        if row.id == image_id:
            return row
    return None


def _set_created_at(image_id: str, value: str) -> None:
    with session() as s:
        row = s.get(Image, image_id)
        assert row is not None
        row.created_at = value


def _set_unreferenced_since(image_id: str, value: str | None) -> None:
    with session() as s:
        row = s.get(Image, image_id)
        assert row is not None
        row.unreferenced_since = value


def _run_sweep() -> None:
    with lightweight_maintenance_queue.immediate_mode():
        sweep_wiki_images()


def test_never_referenced_image_is_flagged_after_grace_then_deleted_after_retention_window(
    tmp_repo,
) -> None:
    image_id = _put_image("guides/orphan.md")
    _set_created_at(image_id, _timestamp_ago(timedelta(hours=25)))

    _run_sweep()

    flagged = _image_row(image_id)
    assert flagged is not None
    assert flagged.unreferenced_since is not None

    _set_unreferenced_since(
        image_id,
        _timestamp_ago(timedelta(days=TRASH_RETENTION_DAYS + 1)),
    )

    _run_sweep()

    assert _image_row(image_id) is None
    assert image_store.stat(image_id) is None


def test_prefix_url_with_longer_hex_tail_is_not_a_reference(tmp_repo) -> None:
    # A URL whose id merely starts with this image's id resolves elsewhere.
    path = "guides/prefix.md"
    image_id = _put_image(path)
    _set_created_at(image_id, _timestamp_ago(timedelta(hours=25)))
    wiki_git.commit_file(
        path, f"![x](/api/wiki/images/{image_id}ab)\n", "seed", author=None
    )

    _run_sweep()

    row = _image_row(image_id)
    assert row is not None
    assert row.unreferenced_since is not None


def test_bare_id_in_page_text_is_not_a_reference(tmp_repo) -> None:
    # Only the full serving URL counts. A coincidental 16-hex string in page
    # text (a sha, a random token) must not keep an orphan alive.
    path = "guides/coincidence.md"
    image_id = _put_image(path)
    _set_created_at(image_id, _timestamp_ago(timedelta(hours=25)))
    wiki_git.commit_file(path, f"hex soup: {image_id}\n", "seed", author=None)

    _run_sweep()

    row = _image_row(image_id)
    assert row is not None
    assert row.unreferenced_since is not None


def test_committed_page_reference_keeps_image_live(tmp_repo) -> None:
    path = "guides/kept.md"
    image_id = _put_image(path)
    _set_created_at(image_id, _timestamp_ago(timedelta(hours=25)))
    wiki_git.commit_file(path, _body_with_image(image_id), "seed", author=None)

    _run_sweep()

    row = _image_row(image_id)
    assert row is not None
    assert row.unreferenced_since is None


def test_active_draft_buffer_reference_keeps_image_live(tmp_repo) -> None:
    path = "drafts/live.md"
    image_id = _put_image(path)
    _set_created_at(image_id, _timestamp_ago(timedelta(hours=25)))
    coedit.open_session(
        path,
        base_sha=None,
        initial_buffer=f"draft keeps ![x](/api/wiki/images/{image_id}) live",
    )

    _run_sweep()

    row = _image_row(image_id)
    assert row is not None
    assert row.unreferenced_since is None


def test_trashed_page_reference_keeps_image_live(tmp_repo) -> None:
    path = "guides/trashed.md"
    image_id = _put_image(path)
    _set_created_at(image_id, _timestamp_ago(timedelta(hours=25)))
    body = _body_with_image(image_id)
    wiki_git.commit_file(path, body, "seed", author=None)

    trash_id = trash.new_trash_id()
    dest = trash.trash_location(trash_id, path)
    wiki_git.move_and_commit(path, dest, body, trash.trash_commit_message(path), author=None)

    _run_sweep()

    row = _image_row(image_id)
    assert row is not None
    assert row.unreferenced_since is None


def test_dereference_flag_is_cleared_when_reference_returns(tmp_repo) -> None:
    path = "guides/flip.md"
    image_id = _put_image(path)
    _set_created_at(image_id, _timestamp_ago(timedelta(hours=25)))

    wiki_git.commit_file(path, _body_with_image(image_id), "seed", author=None)
    _run_sweep()
    initial = _image_row(image_id)
    assert initial is not None
    assert initial.unreferenced_since is None

    wiki_git.commit_file(path, "# Page\nno image here\n", "drop image", author=None)
    _run_sweep()
    flagged = _image_row(image_id)
    assert flagged is not None
    assert flagged.unreferenced_since is not None

    wiki_git.commit_file(path, _body_with_image(image_id), "restore image", author=None)
    _run_sweep()
    cleared = _image_row(image_id)
    assert cleared is not None
    assert cleared.unreferenced_since is None


def test_dereference_retention_deletes_old_flagged_image(tmp_repo) -> None:
    image_id = _put_image("guides/delete-me.md")
    _set_created_at(image_id, _timestamp_ago(timedelta(hours=25)))
    _set_unreferenced_since(
        image_id,
        _timestamp_ago(timedelta(days=TRASH_RETENTION_DAYS + 1)),
    )

    _run_sweep()

    assert _image_row(image_id) is None
    assert image_store.stat(image_id) is None


def test_recent_unreferenced_image_stays_within_creation_grace(tmp_repo) -> None:
    image_id = _put_image("guides/fresh.md")

    _run_sweep()

    row = _image_row(image_id)
    assert row is not None
    assert row.unreferenced_since is None


def test_metrics_refresh_and_upload_rejections_are_counted(tmp_repo) -> None:
    kept_image_id = _put_image("guides/metrics-keep.md")
    deleted_image_id = _put_image("guides/metrics-delete.md")
    _set_created_at(deleted_image_id, _timestamp_ago(timedelta(hours=25)))
    _set_unreferenced_since(
        deleted_image_id,
        _timestamp_ago(timedelta(days=TRASH_RETENTION_DAYS + 1)),
    )

    deleted_before = wiki_image_sweep_deleted_total._value.get()
    _run_sweep()

    assert wiki_image_sweep_deleted_total._value.get() == deleted_before + 1
    assert wiki_images_total._value.get() == 1
    assert wiki_images_bytes_total._value.get() == len(PNG_BYTES)
    assert image_store.stat(kept_image_id) is not None
    assert image_store.stat(deleted_image_id) is None

    client = TestClient(create_app())
    user_id = users_repo.create(email="admin@x.com", password="hunter2-x", name="Admin")
    login_fastapi(client, user_id)
    rejected_before = wiki_image_upload_rejected_total.labels(reason="too_large")._value.get()

    response = client.post(
        "/api/wiki/images?path=guides/metrics-keep.md",
        content=b"\x89PNG\r\n\x1a\n" + b"\x00" * (10 * 1024 * 1024 + 1),
        headers={"Content-Type": "image/png"},
    )

    assert response.status_code == 413
    assert wiki_image_upload_rejected_total.labels(reason="too_large")._value.get() == (
        rejected_before + 1
    )
