"""Retire a page into a survivor — trash-move + identity forwarding.

The retire keeps the standard trash lifecycle (restorable, drops from
search/live, comments ride to trash with the page) and adds one delta: the
doc id forwards to the survivor.
"""
from __future__ import annotations

import pytest

from app.models.wiki import PathMove
from app.wiki import doc_ids, notify, retire, trash
from app.wiki import git as wiki_git


@pytest.fixture
def repo(tmp_repo):
    wiki_git.commit_file("docs/kept.md", "# Kept\n", "seed", author=None)
    wiki_git.commit_file("docs/dup.md", "# Dup\n", "seed", author=None)
    return tmp_repo


def test_retire_trashes_source_and_keeps_target(repo):
    sha = retire.retire_page("docs/dup.md", "docs/kept.md")

    assert sha
    paths = list(wiki_git.list_paths())
    assert "docs/dup.md" not in paths
    assert "docs/kept.md" in paths
    # The source rode into the (list_paths-hidden) trash tree, restorable.
    assert any(
        p.endswith("/docs/dup.md") for p in wiki_git.list_trash_files()
    )


def test_retired_id_resolves_to_the_survivor(repo):
    dup_id = doc_ids.get_or_mint("docs/dup.md")
    kept_id = doc_ids.get_or_mint("docs/kept.md")

    retire.retire_page("docs/dup.md", "docs/kept.md")

    resolved = doc_ids.resolve(dup_id)
    assert resolved is not None
    assert resolved["id"] == kept_id
    assert resolved["path"] == "docs/kept.md"
    assert resolved["deleted_at"] is None  # landed on a live document
    # The raw row is still the tombstone — resolve is what follows the forward.
    raw = doc_ids.get(dup_id)
    assert raw is not None and raw["deleted_at"] is not None
    assert raw["forwarded_to"] == kept_id


def test_forward_chain_resolves_transitively(repo):
    wiki_git.commit_file("docs/final.md", "# Final\n", "seed", author=None)
    a = doc_ids.get_or_mint("docs/dup.md")
    final_id = doc_ids.get_or_mint("docs/final.md")

    retire.retire_page("docs/dup.md", "docs/kept.md")  # A -> B
    retire.retire_page("docs/kept.md", "docs/final.md")  # B -> C

    resolved = doc_ids.resolve(a)
    assert resolved is not None
    assert resolved["id"] == final_id  # A follows through B to C


def test_forward_cycle_stops_instead_of_looping(repo):
    a = doc_ids.get_or_mint("docs/dup.md")
    b = doc_ids.get_or_mint("docs/kept.md")
    # Hand-corrupted state: a cycle. resolve must terminate.
    doc_ids.set_forward(a, b)
    doc_ids.set_forward(b, a)

    resolved = doc_ids.resolve(a)
    assert resolved is not None
    assert resolved["id"] in {a, b}


def test_restore_from_trash_clears_the_forward(repo):
    dup_id = doc_ids.get_or_mint("docs/dup.md")
    retire.retire_page("docs/dup.md", "docs/kept.md")

    # Restore exactly as the API route does: git move back, re-point
    # metadata, re-bind the tombstoned ids.
    entry = next(
        e for e in trash.list_entries() if e.original_path == "docs/dup.md"
    )
    sha, moves = wiki_git.restore_from_trash(entry.trash_id, "restore", author=None)
    notify.after_path_move(
        moves,
        sha,
        None,
        root_move=PathMove(
            old=trash.trash_location(entry.trash_id, entry.original_path),
            new=entry.original_path,
        ),
    )
    doc_ids.on_restored([mv.new for mv in moves])

    assert "docs/dup.md" in wiki_git.list_paths()  # back from trash
    resolved = doc_ids.resolve(dup_id)
    assert resolved is not None
    assert resolved["id"] == dup_id  # live again, no forward followed
    assert resolved["path"] == "docs/dup.md"
    assert resolved["forwarded_to"] is None


def test_set_forward_refuses_unknown_target(repo):
    # A dangling forward would silently defeat the retirement (the id would
    # resolve to the tombstone, not the survivor) — refused up front.
    a = doc_ids.get_or_mint("docs/dup.md")
    with pytest.raises(ValueError, match="unknown forward target"):
        doc_ids.set_forward(a, "no-such-id")


def test_retire_validations(repo):
    with pytest.raises(ValueError, match="same page"):
        retire.retire_page("docs/dup.md", "docs/dup.md")
    with pytest.raises(ValueError, match="not found"):
        retire.retire_page("docs/missing.md", "docs/kept.md")
    with pytest.raises(ValueError, match="not found"):
        retire.retire_page("docs/dup.md", "docs/missing.md")
    with pytest.raises(ValueError, match=r"\.md"):
        retire.retire_page("docs", "docs/kept.md")
