"""Folder-level ACL/policy re-point on move.

The move callers pass the real rename as `root_move`. A folder `root_move`
drives the folder-prefix swap; a `.md`-page `root_move` is a single page move,
so no folder row is touched. This matters both ways:

- Deep-only folder: a folder whose files all sit in one subdirectory. Inference
  from the file moves finds only the *deepest* shared prefix and would strand
  the folder's own row; `root_move` re-points it correctly.
- Single cross-folder file move: inference can't tell it from a folder rename
  and would rewrite the source folder's row onto the destination; the page
  `root_move` suppresses the folder-prefix swap entirely.
"""
from __future__ import annotations

from app.models.wiki import PathMove
from app.wiki import acl, update_policy


def _folder_paths(path: str) -> set[str]:
    """resource_paths of folder ACL rows that apply to ``path``."""
    return {
        g["resource_path"]
        for g in acl.list_for_path(path)
        if g["resource_kind"] == "folder"
    }


def test_folder_acl_repoints_with_root_move_deep_only(tmp_db):
    # Folder grant on `proj`; its only file lives under `proj/sub/`.
    acl.grant(
        resource_kind="folder",
        resource_path="proj",
        principal_kind="everyone",
        principal_id=None,
        permission="read",
        granted_by_user_id=None,
    )
    moves = [PathMove(old="proj/sub/a.md", new="proj2/sub/a.md")]

    acl.on_path_moved(moves, root_move=PathMove(old="proj", new="proj2"))

    # The folder grant followed to proj2 — not stranded at the old `proj`.
    assert "proj2" in _folder_paths("proj2/sub/a.md")
    assert "proj" not in _folder_paths("proj/sub/a.md")


def test_without_root_move_the_deep_prefix_is_missed(tmp_db):
    # Control: inference alone (no root_move) can't see the `proj` root, so the
    # folder grant is NOT re-pointed — this is the bug root_move fixes.
    acl.grant(
        resource_kind="folder",
        resource_path="proj",
        principal_kind="everyone",
        principal_id=None,
        permission="read",
        granted_by_user_id=None,
    )
    acl.on_path_moved([PathMove(old="proj/sub/a.md", new="proj2/sub/a.md")])
    assert "proj" in _folder_paths("proj/sub/a.md")  # stranded without root_move


def test_page_move_between_folders_leaves_folder_acl_untouched(tmp_db):
    # A folder grant on each of two sibling folders. Moving a single page from
    # one to the other must not rewrite the source folder's grant onto the
    # destination — the page root_move suppresses the folder-prefix swap.
    for folder in ("scratch", "dest"):
        acl.grant(
            resource_kind="folder",
            resource_path=folder,
            principal_kind="everyone",
            principal_id=None,
            permission="read",
            granted_by_user_id=None,
        )
    acl.on_path_moved(
        [PathMove(old="scratch/xrep.md", new="dest/xrep.md")],
        root_move=PathMove(old="scratch/xrep.md", new="dest/xrep.md"),
    )
    assert "scratch" in _folder_paths("scratch/other.md")
    assert "dest" in _folder_paths("dest/xrep.md")


def test_page_move_between_folders_leaves_policy_rows_untouched(tmp_db):
    update_policy.set_policy("scratch", ingestion_auto_update_disabled=True)
    update_policy.set_policy("dest", ingestion_auto_update_disabled=True)
    update_policy.on_path_moved(
        [PathMove(old="scratch/xrep.md", new="dest/xrep.md")],
        root_move=PathMove(old="scratch/xrep.md", new="dest/xrep.md"),
    )
    assert update_policy.get("scratch") is not None
    assert update_policy.get("dest") is not None


def test_update_policy_folder_repoints_with_root_move(tmp_db):
    update_policy.set_policy("proj", ingestion_auto_update_disabled=True)
    # A nested-folder row exercises the LIKE "proj/%" branch of the rewrite.
    update_policy.set_policy("proj/sub", ingestion_auto_update_disabled=True)
    update_policy.on_path_moved(
        [PathMove(old="proj/sub/a.md", new="proj2/sub/a.md")],
        root_move=PathMove(old="proj", new="proj2"),
    )
    assert update_policy.get("proj2") is not None
    assert update_policy.get("proj") is None
    assert update_policy.get("proj2/sub") is not None
    assert update_policy.get("proj/sub") is None
