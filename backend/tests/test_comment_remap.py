"""Real-time comment re-anchoring (app/wiki/comment_remap.py).

Integration-level: commits real bodies to a tmp wiki repo, anchors a comment at
one commit, edits the page, then asserts the comment drifted or orphaned. Uses
``tmp_repo`` (DB + initialized git repo) and drives ``remap_comments`` directly
(it's synchronous), rather than going through the notify hook.
"""
from __future__ import annotations

from typing import Any

from app.wiki import comment_remap, comments
from app.wiki import git as wiki_git

_PATH = "notes.md"


def _anchor(path: str, sha: str, body: str, phrase: str) -> dict[str, Any]:
    start = body.index(phrase)
    return comments.create_thread(
        doc_path=path,
        body="is this still right?",
        author_user_id=None,
        anchor_sha=sha,
        start_offset=start,
        end_offset=start + len(phrase),
        quoted_text=phrase,
    )


def test_edit_before_span_drifts_anchor_to_head(tmp_repo):
    body1 = "Intro paragraph.\nThe target sentence stays put.\n"
    sha1 = wiki_git.commit_file(_PATH, body1, "create")
    c = _anchor(_PATH, sha1, body1, "target sentence")

    body2 = "Intro paragraph, now considerably longer.\nThe target sentence stays put.\n"
    sha2 = wiki_git.commit_file(_PATH, body2, "edit")
    comment_remap.remap_comments(_PATH)

    got = comments.get(c["id"])
    assert got is not None
    assert got["status"] == "open"
    assert got["anchor_sha"] == sha2  # advanced to HEAD
    # offsets shifted, and now point at the same text in the new body
    assert body2[got["start_offset"] : got["end_offset"]] == "target sentence"
    assert got["quoted_text"] == "target sentence"


def test_deleted_anchor_orphans_and_freezes_tombstone(tmp_repo):
    body1 = "Keep first line.\nThis sentence will be removed entirely.\nKeep last line.\n"
    sha1 = wiki_git.commit_file(_PATH, body1, "create")
    c = _anchor(_PATH, sha1, body1, "This sentence will be removed entirely")
    start = c["start_offset"]

    body2 = "Keep first line.\nKeep last line.\n"
    wiki_git.commit_file(_PATH, body2, "edit")
    comment_remap.remap_comments(_PATH)

    got = comments.get(c["id"])
    assert got is not None
    assert got["status"] == "orphaned"
    # tombstone frozen at the last good anchor
    assert got["anchor_sha"] == sha1
    assert got["start_offset"] == start
    assert got["quoted_text"] == "This sentence will be removed entirely"


def test_unchanged_span_keeps_exact_offsets(tmp_repo):
    body1 = "Alpha.\nBeta sentence to comment on.\nGamma.\n"
    sha1 = wiki_git.commit_file(_PATH, body1, "create")
    c = _anchor(_PATH, sha1, body1, "Beta sentence to comment on")
    s, e = c["start_offset"], c["end_offset"]

    # Edit only the last line; the commented span is untouched.
    body2 = "Alpha.\nBeta sentence to comment on.\nGamma extended with more words.\n"
    sha2 = wiki_git.commit_file(_PATH, body2, "edit")
    comment_remap.remap_comments(_PATH)

    got = comments.get(c["id"])
    assert got is not None
    assert (got["start_offset"], got["end_offset"]) == (s, e)  # unchanged
    assert got["anchor_sha"] == sha2


def test_already_at_head_is_noop(tmp_repo):
    body = "A line worth commenting on.\n"
    sha1 = wiki_git.commit_file(_PATH, body, "create")
    c = _anchor(_PATH, sha1, body, "worth commenting")

    comment_remap.remap_comments(_PATH)  # HEAD == anchor_sha already

    got = comments.get(c["id"])
    assert got is not None
    assert got["anchor_sha"] == sha1
    assert (got["start_offset"], got["end_offset"]) == (c["start_offset"], c["end_offset"])


def test_noop_without_comments_or_non_md(tmp_repo):
    wiki_git.commit_file("empty.md", "nothing anchored here\n", "create")
    comment_remap.remap_comments("empty.md")  # no comments -> no-op, no raise
    comment_remap.remap_comments("notes.txt")  # non-.md -> ignored
