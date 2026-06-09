"""Comment full-text search — the `wiki-comments` index and its lifecycle.

The pure token helpers run anywhere; the index/search tests need OpenSearch and
are skipped when it isn't reachable (``needs_opensearch``). Comment mutations
index inline, so we assert against ``comment_fts.search`` right after writing.
"""
from __future__ import annotations

from app.db import comment_fts
from app.wiki import comment_mentions, comments
from tests._seed import seed_user
from tests.conftest import needs_opensearch

_DOC = "guides/db.md"


def _root(author: str, body: str, *, doc: str = _DOC) -> dict:
    return comments.create_thread(
        doc_path=doc,
        body=body,
        author_user_id=author,
        anchor_sha="sha1",
        start_offset=0,
        end_offset=5,
        quoted_text="pool ",
    )


# --- pure helpers (no OpenSearch) ----------------------------------------- #


def test_detokenize_renders_mentions_as_names():
    body = "hey @[Bo Yang](mention:cmt_abc) check @[Admin](mention:cmt_def)"
    assert comment_mentions.detokenize(body) == "hey @Bo Yang check @Admin"


def test_mentioned_ids_are_distinct_and_ordered():
    body = "@[Bo](mention:u1) and @[Al](mention:u2) and @[Bo again](mention:u1)"
    assert comment_mentions.mentioned_ids(body) == ["u1", "u2"]


def test_detokenize_noop_without_mentions():
    assert comment_mentions.detokenize("plain text, no mentions") == (
        "plain text, no mentions"
    )


# --- index + search lifecycle (OpenSearch) -------------------------------- #


@needs_opensearch
def test_new_comment_is_searchable(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    _root(alice, "check the connection pool size in the config")

    hits = comment_fts.search("connection pool", user_id=alice, is_admin=True)
    assert len(hits) == 1
    assert hits[0].doc_path == _DOC
    # Non-matching query returns nothing.
    assert comment_fts.search("kubernetes ingress", user_id=alice, is_admin=True) == []


@needs_opensearch
def test_reply_is_searchable_and_deep_links_to_thread(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    root = _root(alice, "root about caching")
    comments.add_reply(parent_id=root["id"], body="use a redis sidecar", author_user_id=alice)

    hits = comment_fts.search("redis sidecar", user_id=alice, is_admin=True)
    assert len(hits) == 1
    # Replies point back at the thread root so the UI can deep-link.
    assert hits[0].thread_root_id == root["id"]


@needs_opensearch
def test_resolved_searchable_orphaned_not(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    root = _root(alice, "throttle the webhook retries")

    comments.set_thread_status(root["id"], "resolved", resolved_by_user_id=alice)
    assert len(comment_fts.search("throttle webhook", user_id=alice, is_admin=True)) == 1

    comments.orphan(root["id"])
    assert comment_fts.search("throttle webhook", user_id=alice, is_admin=True) == []


@needs_opensearch
def test_edit_updates_the_index(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    root = _root(alice, "original wording about quotas")

    comments.edit_body(root["id"], "rewritten to mention rate limiting")
    assert comment_fts.search("rate limiting", user_id=alice, is_admin=True)
    assert comment_fts.search("quotas", user_id=alice, is_admin=True) == []


@needs_opensearch
def test_delete_removes_from_index(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    root = _root(alice, "delete me about migrations")

    assert comment_fts.search("migrations", user_id=alice, is_admin=True)
    comments.delete(root["id"])
    assert comment_fts.search("migrations", user_id=alice, is_admin=True) == []


@needs_opensearch
def test_mention_is_searchable_by_name(tmp_db):
    alice = seed_user(uid="u_alice", email="alice@x.com")
    _root(alice, "ping @[Bo Yang](mention:cmt_bo) about the schema")

    # The token is detokenized before indexing, so the display name matches.
    hits = comment_fts.search("Bo Yang", user_id=alice, is_admin=True)
    assert len(hits) == 1
