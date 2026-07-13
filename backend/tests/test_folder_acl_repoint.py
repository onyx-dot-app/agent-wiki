"""Folder-level ACL/policy re-point on move, including the deep-only case.

`acl.on_path_moved` / `update_policy.on_path_moved` infer the folder-prefix
swap from the file moves, but that finds the *deepest* shared prefix — so a
folder whose files all sit in one subdirectory would leave the folder's own
row stranded. The move callers pass the real rename as `root_move` to fix it.
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
