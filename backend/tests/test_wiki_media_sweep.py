"""Retention sweep tests for wiki media."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.auth import users as users_repo
from app.db.models import Media
from app.db.session import session
from app.main import create_app
from app.metrics import (
    wiki_media_sweep_deleted_total,
    wiki_media_upload_rejected_total,
    wiki_media_bytes_total,
    wiki_media_total,
)
from app.tasks.media_sweep import sweep_wiki_media
from app.tasks.queues import lightweight_maintenance_queue
from app.tasks.trash_purge import TRASH_RETENTION_DAYS
from app.wiki import coedit, doc_ids, git as wiki_git, media_store, trash
from app.wiki.markdown_yjs import seed_doc_from_markdown

from tests._auth import login_fastapi

from app.tasks.media_sweep import _TEXT_TIMESTAMP_FORMAT as _TS_FORMAT
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _timestamp_ago(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) - delta).strftime(_TS_FORMAT)


def _put_image(path: str) -> str:
    return media_store.put(
        data=PNG_BYTES,
        content_type="image/png",
        anchor_doc_id=doc_ids.get_or_mint(path),
        uploaded_by=None,
    )


def _body_with_image(image_id: str) -> str:
    return f"# Page\n![x]({media_store.serving_url(image_id)})\n"


def _image_row(image_id: str) -> media_store.MediaSweepRow | None:
    for row in media_store.list_for_sweep():
        if row.id == image_id:
            return row
    return None


def _set_created_at(image_id: str, value: str) -> None:
    with session() as s:
        row = s.get(Media, image_id)
        assert row is not None
        row.created_at = value




def _open_draft_citing(path: str, image_id: str) -> None:
    """Open a live session on ``path`` whose draft cites ``image_id``."""
    body = _body_with_image(image_id)
    sess = coedit.open_session(path, base_sha=None)
    doc = seed_doc_from_markdown(body)
    assert coedit.set_initial_snapshot(sess.id, doc.get_update(), body)


def _run_sweep() -> None:
    with lightweight_maintenance_queue.immediate_mode():
        sweep_wiki_media()


def test_never_referenced_image_is_flagged_after_grace_then_deleted_after_retention_window(
    tmp_repo,
) -> None:
    image_id = _put_image("guides/orphan.md")
    _set_created_at(image_id, _timestamp_ago(timedelta(hours=25)))

    _run_sweep()

    flagged = _image_row(image_id)
    assert flagged is not None
    assert flagged.unreferenced_since is not None

    media_store.set_unreferenced_since(
        image_id,
        _timestamp_ago(timedelta(days=TRASH_RETENTION_DAYS + 1)),
    )

    _run_sweep()

    assert _image_row(image_id) is None
    assert media_store.stat(image_id) is None


def test_session_on_another_page_does_not_keep_image_live(tmp_repo) -> None:
    # A session whose draft does not cite the image must not keep it alive,
    # or any one open editor would pin every orphan in the wiki.
    image_id = _put_image("guides/anchored.md")
    _set_created_at(image_id, _timestamp_ago(timedelta(hours=25)))
    media_store.set_unreferenced_since(image_id, _timestamp_ago(timedelta(days=31)))
    coedit.open_session("guides/elsewhere.md", base_sha=None)

    _run_sweep()

    assert _image_row(image_id) is None


def test_non_hex_url_suffix_is_not_a_reference(tmp_repo) -> None:
    # A .png tail extends the URL path, so it resolves to a different route.
    path = "guides/suffix.md"
    image_id = _put_image(path)
    _set_created_at(image_id, _timestamp_ago(timedelta(hours=25)))
    wiki_git.commit_file(
        path, f"![x](/api/wiki/media/{image_id}.png)\n", "seed", author=None
    )

    _run_sweep()

    row = _image_row(image_id)
    assert row is not None
    assert row.unreferenced_since is not None


def test_uppercase_hex_tail_is_not_a_reference(tmp_repo) -> None:
    # The boundary is case-insensitive hex, so an uppercase tail is still a
    # longer different identifier, not this image's reference.
    path = "guides/upper.md"
    image_id = _put_image(path)
    _set_created_at(image_id, _timestamp_ago(timedelta(hours=25)))
    wiki_git.commit_file(
        path, f"![x](/api/wiki/media/{image_id}AB)\n", "seed", author=None
    )

    _run_sweep()

    row = _image_row(image_id)
    assert row is not None
    assert row.unreferenced_since is not None


def test_prefix_url_with_longer_hex_tail_is_not_a_reference(tmp_repo) -> None:
    # A URL whose id merely starts with this image's id resolves elsewhere.
    path = "guides/prefix.md"
    image_id = _put_image(path)
    _set_created_at(image_id, _timestamp_ago(timedelta(hours=25)))
    wiki_git.commit_file(
        path, f"![x](/api/wiki/media/{image_id}ab)\n", "seed", author=None
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


def test_draft_on_the_anchor_page_keeps_image_live(tmp_repo) -> None:
    # An uncommitted draft citing the image is the only thing keeping it
    # reachable, and no working-tree scan can see it.
    path = "drafts/live.md"
    image_id = _put_image(path)
    _set_created_at(image_id, _timestamp_ago(timedelta(hours=25)))
    _open_draft_citing(path, image_id)

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
    media_store.set_unreferenced_since(
        image_id,
        _timestamp_ago(timedelta(days=TRASH_RETENTION_DAYS + 1)),
    )

    _run_sweep()

    assert _image_row(image_id) is None
    assert media_store.stat(image_id) is None


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
    media_store.set_unreferenced_since(
        deleted_image_id,
        _timestamp_ago(timedelta(days=TRASH_RETENTION_DAYS + 1)),
    )

    deleted_before = wiki_media_sweep_deleted_total._value.get()
    _run_sweep()

    assert wiki_media_sweep_deleted_total._value.get() == deleted_before + 1
    assert wiki_media_total._value.get() == 1
    assert wiki_media_bytes_total._value.get() == len(PNG_BYTES)
    assert media_store.stat(kept_image_id) is not None
    assert media_store.stat(deleted_image_id) is None

    client = TestClient(create_app())
    user_id = users_repo.create(email="admin@x.com", password="hunter2-x", name="Admin")
    login_fastapi(client, user_id)
    rejected_before = wiki_media_upload_rejected_total.labels(reason="too_large")._value.get()

    response = client.post(
        "/api/wiki/media?path=guides/metrics-keep.md",
        content=b"\x89PNG\r\n\x1a\n" + b"\x00" * (10 * 1024 * 1024 + 1),
        headers={"Content-Type": "image/png"},
    )

    assert response.status_code == 413
    assert wiki_media_upload_rejected_total.labels(reason="too_large")._value.get() == (
        rejected_before + 1
    )


def test_draft_on_another_page_keeps_a_pasted_image_live(tmp_repo) -> None:
    # An image is reachable from any page it was pasted into, not only the one
    # it was uploaded against, so the anchor cannot stand in for its citations.
    image_id = _put_image("guides/anchored.md")
    _set_created_at(image_id, _timestamp_ago(timedelta(hours=25)))
    media_store.set_unreferenced_since(image_id, _timestamp_ago(timedelta(days=31)))
    _open_draft_citing("guides/elsewhere.md", image_id)

    _run_sweep()

    row = _image_row(image_id)
    assert row is not None
    assert row.unreferenced_since is None
    assert media_store.stat(image_id) is not None


def test_a_draft_edit_during_the_rescan_defers_the_delete(tmp_repo, monkeypatch) -> None:
    # Drafts are not under the commit lock, so a citation added between the
    # re-scan and the delete would otherwise lose its blob.
    media_id = _put_image("guides/anchored.md")
    _set_created_at(media_id, _timestamp_ago(timedelta(hours=25)))
    media_store.set_unreferenced_since(media_id, _timestamp_ago(timedelta(days=31)))
    sess = coedit.open_session("guides/other.md", base_sha=None)

    # Read order inside the lock is: batch draft scan, `before`, re-scan,
    # then the comparison. Only the last read sees the simulated edit.
    real = coedit.active_session_versions
    calls: list[int] = []

    def bumping() -> dict[int, int]:
        calls.append(1)
        versions = real()
        if len(calls) < 4:
            return versions
        return {sid: seq + 1 for sid, seq in versions.items()} or {sess.id: 1}

    monkeypatch.setattr(coedit, "active_session_versions", bumping)
    _run_sweep()

    row = _image_row(media_id)
    assert row is not None, "a draft edit in the window must defer, not delete"
    assert media_store.stat(media_id) is not None
