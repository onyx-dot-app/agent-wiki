"""Checkpoint engine (app/wiki/coedit_checkpoint.py) — splices a session's
live Yjs doc and commits it to git via the merge gateway, with last-editor
author + co-author trailers. DB + real git (the ``tmp_repo`` fixture).

``checkpoint_ydoc_session`` takes the live ``pycrdt.Doc``/``TouchedTracker``
directly (they only ever live in the process holding the room — see the
module docstring) rather than resolving them from a session id, so these
tests build a ``Doc`` via ``seed_doc_from_markdown`` and edit it directly,
mirroring ``test_markdown_splice.py``'s pattern instead of going through a
live WS connection.
"""
from __future__ import annotations

import asyncio

import pycrdt
import pytest

from app.auth import users as users_repo
from app.db.models import DocumentTemplate
from app.db.session import session as db_session
from app.wiki import coedit, coedit_checkpoint, drafts
from app.wiki import git as wiki_git
from app.wiki.markdown_splice import TouchedTracker
from app.wiki.markdown_yjs import ROOT_XML_KEY, seed_doc_from_markdown

_PATH = "guides/setup.md"


def _seed_page(body: str) -> str:
    return wiki_git.commit_file(_PATH, body, "seed", author="Seed <seed@x.com>")


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def repo(tmp_repo):
    return tmp_repo


def test_checkpoint_commits_doc_and_credits_coauthors(repo):
    ada = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    bo = users_repo.create(email="bo@x.com", password="hunter2-x", name="Bo")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, ada)
    coedit.join(sess.id, bo)

    doc = seed_doc_from_markdown("hello world")
    tracker = TouchedTracker(doc)
    root = doc.get(ROOT_XML_KEY, type=pycrdt.XmlFragment)
    with doc.transaction():
        root.children[0].children[0].insert(0, "hi ")
    coedit.append_ydoc_update(sess.id, update_bytes=bytes(doc.get_update()), author_user_id=ada)

    new_sha = _run(
        coedit_checkpoint.checkpoint_ydoc_session(
            sess.id, doc=doc, tracker=tracker, author_user_id=ada
        )
    )

    assert new_sha is not None
    assert wiki_git.read_file(_PATH) == "hi hello world"
    st = coedit.get_ydoc_state(sess.id)
    assert st is not None
    assert st.checkpointed_seq == st.seq
    assert st.base_sha == new_sha
    commit = wiki_git.history(_PATH)[0]
    assert commit.author == "Ada"
    assert "Co-authored-by: Bo <bo@x.com>" in commit.message
    assert "ada@x.com" not in commit.message


def test_checkpoint_merges_concurrent_agent_commit(repo):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    body = "one\ntwo\nthree\nfour\nfive\n"
    sha = _seed_page(body)
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)

    doc = seed_doc_from_markdown(body)
    tracker = TouchedTracker(doc)
    root = doc.get(ROOT_XML_KEY, type=pycrdt.XmlFragment)
    with doc.transaction():
        root.children[0].children[0].insert(0, "ONE ")
    coedit.append_ydoc_update(sess.id, update_bytes=bytes(doc.get_update()), author_user_id=uid)

    # Distant, non-overlapping agent commit lands out of band.
    wiki_git.commit_file(
        _PATH, "one\ntwo\nthree\nfour\nFIVE\n", "agent edit", author="Agent <a@x.com>"
    )

    new_sha = _run(
        coedit_checkpoint.checkpoint_ydoc_session(
            sess.id, doc=doc, tracker=tracker, author_user_id=uid
        )
    )
    assert new_sha is not None
    assert wiki_git.read_file(_PATH) == "ONE one\ntwo\nthree\nfour\nFIVE\n"


def test_checkpoint_is_noop_when_clean(repo):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)

    doc = seed_doc_from_markdown("hello world")
    tracker = TouchedTracker(doc)
    root = doc.get(ROOT_XML_KEY, type=pycrdt.XmlFragment)
    with doc.transaction():
        root.children[0].children[0].insert(0, "hi ")
    coedit.append_ydoc_update(sess.id, update_bytes=bytes(doc.get_update()), author_user_id=uid)

    assert (
        _run(
            coedit_checkpoint.checkpoint_ydoc_session(
                sess.id, doc=doc, tracker=tracker, author_user_id=uid
            )
        )
        is not None
    )
    # Nothing new since the last checkpoint → no second commit.
    assert (
        _run(
            coedit_checkpoint.checkpoint_ydoc_session(
                sess.id, doc=doc, tracker=tracker, author_user_id=uid
            )
        )
        is None
    )


def test_checkpoint_skips_closed_dirty_session(repo):
    # A closed session with un-checkpointed edits must NOT be re-committed —
    # it would clobber newer HEAD with a stale doc.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)

    doc = seed_doc_from_markdown("hello world")
    tracker = TouchedTracker(doc)
    root = doc.get(ROOT_XML_KEY, type=pycrdt.XmlFragment)
    with doc.transaction():
        root.children[0].children[0].insert(0, "hi ")
    coedit.append_ydoc_update(sess.id, update_bytes=bytes(doc.get_update()), author_user_id=uid)

    newer = wiki_git.commit_file(_PATH, "hello world v2", "agent", author="A <a@x.com>")
    coedit.close_session(sess.id)  # closed while still dirty

    result = _run(
        coedit_checkpoint.checkpoint_ydoc_session(
            sess.id, doc=doc, tracker=tracker, author_user_id=uid
        )
    )
    assert result is None
    assert wiki_git.head_sha_for_path(_PATH) == newer
    assert wiki_git.read_file(_PATH) == "hello world v2"


def test_checkpoint_skips_when_path_missing(repo):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    sha = _seed_page("hello world")
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)

    doc = seed_doc_from_markdown("hello world")
    tracker = TouchedTracker(doc)
    root = doc.get(ROOT_XML_KEY, type=pycrdt.XmlFragment)
    with doc.transaction():
        root.children[0].children[0].insert(0, "hi ")
    coedit.append_ydoc_update(sess.id, update_bytes=bytes(doc.get_update()), author_user_id=uid)

    # The page moves away underneath the session without a re-key (a raw-git
    # rename bypassing the lifecycle hook, or the pre-fix bug).
    wiki_git.move_path(_PATH, "guides/renamed.md", "rename")

    result = _run(
        coedit_checkpoint.checkpoint_ydoc_session(
            sess.id, doc=doc, tracker=tracker, author_user_id=uid
        )
    )
    assert result is None
    assert wiki_git.read_file_opt(_PATH) is None
    after = coedit.get_ydoc_state(sess.id)
    assert after is not None
    assert after.checkpointed_seq != after.seq  # still dirty — left for manual recovery


def test_checkpoint_creates_brand_new_page(repo):
    uid = users_repo.create(email="new@x.com", password="hunter2-x", name="New")
    sess = coedit.open_session("guides/fresh.md", base_sha=None)
    coedit.join(sess.id, uid)

    doc = seed_doc_from_markdown("")
    tracker = TouchedTracker(doc)
    new_para = pycrdt.XmlElement("paragraph", {"_blockId": "b0", "_nl": "1"}, contents=[])
    root = doc.get(ROOT_XML_KEY, type=pycrdt.XmlFragment)
    with doc.transaction():
        root.children.append(new_para)
    with doc.transaction():
        new_para.children.append(pycrdt.XmlText("first words"))
    coedit.append_ydoc_update(sess.id, update_bytes=bytes(doc.get_update()), author_user_id=uid)

    result = _run(
        coedit_checkpoint.checkpoint_ydoc_session(
            sess.id, doc=doc, tracker=tracker, author_user_id=uid
        )
    )
    assert result is not None
    assert wiki_git.read_file_opt("guides/fresh.md") == "first words\n"


def test_checkpoint_clears_template_draft_when_body_diverges(repo):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    tmpl_body = "# Template\n"
    with db_session() as s:
        s.add(DocumentTemplate(id="tmpl_x", name="X", body=tmpl_body))
    sha = _seed_page(tmpl_body)
    drafts.create(
        path=_PATH, template_id="tmpl_x", template_body_snapshot=tmpl_body, created_by_user_id=uid
    )
    sess = coedit.open_session(_PATH, base_sha=sha)
    coedit.join(sess.id, uid)

    doc = seed_doc_from_markdown(tmpl_body)
    tracker = TouchedTracker(doc)
    root = doc.get(ROOT_XML_KEY, type=pycrdt.XmlFragment)
    with doc.transaction():
        root.children[0].children[0].insert(0, "My ")
    coedit.append_ydoc_update(sess.id, update_bytes=bytes(doc.get_update()), author_user_id=uid)

    _run(
        coedit_checkpoint.checkpoint_ydoc_session(
            sess.id, doc=doc, tracker=tracker, author_user_id=uid
        )
    )

    assert drafts.get(_PATH) is None  # diverged from the template → row cleared
