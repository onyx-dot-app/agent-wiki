"""Comment lifecycle wiring in ``app/wiki/notify.py``.

The repo + remap functions are tested in isolation elsewhere; these tests assert
that the post-write hooks actually invoke them — a doc write re-anchors, a delete
orphans, a move re-keys, and a move out of ``.md``-space orphans.
"""
from __future__ import annotations

from typing import Any

from app.models.wiki import ChangeKind
from app.wiki import comments, notify
from app.wiki import git as wiki_git


def _seed_comment(path: str, sha: str, body: str, phrase: str) -> dict[str, Any]:
    start = body.index(phrase)
    return comments.create_thread(
        doc_path=path,
        body="anchored here?",
        author_user_id=None,
        anchor_sha=sha,
        start_offset=start,
        end_offset=start + len(phrase),
        quoted_text=phrase,
    )


def test_after_doc_write_remaps_comments(tmp_repo):
    v1 = "Alpha beta. The target phrase stays. End."
    sha1 = wiki_git.commit_file("a.md", v1, "seed", author=None)
    c = _seed_comment("a.md", sha1, v1, "target phrase stays")

    v2 = "The target phrase stays. End."  # dropped the leading "Alpha beta. "
    sha2 = wiki_git.commit_file("a.md", v2, "edit", author=None)
    notify.after_doc_write("a.md", sha2, ChangeKind.EDIT, actor=None)

    got = comments.get(c["id"])
    assert got is not None
    assert got["anchor_sha"] == sha2  # advanced to HEAD
    assert v2[got["start_offset"] : got["end_offset"]] == "target phrase stays"


def test_after_doc_delete_orphans_comments(tmp_repo):
    body = "Keep this sentence here."
    sha = wiki_git.commit_file("a.md", body, "seed", author=None)
    c = _seed_comment("a.md", sha, body, "sentence")

    notify.after_doc_delete("a.md", sha, actor=None)

    got = comments.get(c["id"])
    assert got is not None
    assert got["status"] == "orphaned"


def test_after_path_move_md_to_md_rekeys_comments(tmp_repo):
    body = "Keep this sentence here."
    sha = wiki_git.commit_file("a.md", body, "seed", author=None)
    c = _seed_comment("a.md", sha, body, "sentence")

    notify.after_path_move([("a.md", "b.md")], sha, actor=None)

    assert comments.list_for_doc("a.md") == []
    moved = comments.get(c["id"])
    assert moved is not None
    assert moved["doc_path"] == "b.md"


def test_after_path_move_md_to_non_md_orphans_comments(tmp_repo):
    body = "Keep this sentence here."
    sha = wiki_git.commit_file("a.md", body, "seed", author=None)
    c = _seed_comment("a.md", sha, body, "sentence")

    notify.after_path_move([("a.md", "notes.txt")], sha, actor=None)

    got = comments.get(c["id"])
    assert got is not None
    assert got["status"] == "orphaned"
    assert got["doc_path"] == "a.md"  # not re-keyed — there's no new doc
