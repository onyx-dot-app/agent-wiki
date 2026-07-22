"""Retire a page into a survivor — trash-move + identity forwarding.

The retire keeps the standard trash lifecycle (restorable, drops from
search/live) but redirects the page's *references* at the survivor: the doc
id forwards and comment threads re-anchor.
"""
from __future__ import annotations

import pytest

from app.models.wiki import PathMove
from app.wiki import comments, doc_ids, notify, retire, trash
from app.wiki import git as wiki_git
from tests._seed import seed_user


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


def test_comments_reanchor_to_the_survivor(repo):
    uid = seed_user(uid="u1", email="u@x.com")
    comments.create_thread(
        doc_path="docs/dup.md",
        body="does this apply to v2?",
        author_user_id=uid,
        anchor_sha=None,
        start_offset=None,
        end_offset=None,
        quoted_text=None,
        scope="page",
    )

    retire.retire_page("docs/dup.md", "docs/kept.md")

    assert comments.list_for_doc("docs/dup.md") == []
    moved = comments.list_for_doc("docs/kept.md")
    assert len(moved) == 1
    assert moved[0]["body"] == "does this apply to v2?"


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


def test_retire_validations(repo):
    with pytest.raises(ValueError, match="same page"):
        retire.retire_page("docs/dup.md", "docs/dup.md")
    with pytest.raises(ValueError, match="not found"):
        retire.retire_page("docs/missing.md", "docs/kept.md")
    with pytest.raises(ValueError, match="not found"):
        retire.retire_page("docs/dup.md", "docs/missing.md")
    with pytest.raises(ValueError, match=r"\.md"):
        retire.retire_page("docs", "docs/kept.md")
