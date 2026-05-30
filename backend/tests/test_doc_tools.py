"""End-to-end tests for the doc-edit tools (write_doc, edit_doc, multi_edit).

Each tool runs against a tmp wiki git repo. We stub the trigger
fan-out task (its internals have their own coverage in
``test_triggers_fanout.py``).
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def repo_with_doc(tmp_repo, tmp_config):
    """Tmp wiki repo with a single committed doc at ``guide.md``."""
    from app.wiki import git as wiki_git

    body = "# Guide\n\nAlpha section.\n\nBeta section.\n"
    wiki_git.commit_file("guide.md", body, "seed", author=None)
    return tmp_config


@pytest.fixture(autouse=True)
def _stub_side_effects(monkeypatch):
    """Skip task fan-out + trigger eval for these tests.

    The post-write seam is ``app.wiki.notify.after_doc_write`` — patching
    it short-circuits both the FTS reindex and the trigger fan-out task
    enqueue. Coverage of the side effects themselves lives in
    ``test_save_to_fire_e2e.py`` and ``test_triggers_fanout.py``.
    """
    monkeypatch.setattr(
        "app.wiki.utils.wiki_notify.after_doc_write",
        lambda *a, **kw: None,
    )


# --------------------------------------------------------------------------- #
# write_doc                                                                   #
# --------------------------------------------------------------------------- #


def test_write_doc_creates_new_file(repo_with_doc):
    from app.llm.agents.tools.write_doc import handle

    out = handle({"path": "new.md", "body": "# New\n", "commit_message": "create"})
    assert "error" not in out, out
    assert out["created"] is True
    assert Path(repo_with_doc.wiki_dir, "new.md").read_text() == "# New\n"


def test_write_doc_overwrites_with_base_sha(repo_with_doc):
    from app.llm.agents.tools.write_doc import handle
    from app.wiki import git as wiki_git

    base = wiki_git.head_sha_for_path("guide.md")
    out = handle(
        {
            "path": "guide.md",
            "body": "# Replaced\n",
            "commit_message": "rewrite",
            "base_sha": base,
        }
    )
    assert "error" not in out, out
    assert out["created"] is False
    assert Path(repo_with_doc.wiki_dir, "guide.md").read_text() == "# Replaced\n"


def test_write_doc_overwrite_without_base_sha_is_rejected(repo_with_doc):
    """Full-body overwrite has no fuzzy fallback, so every overwrite
    must opt in via ``base_sha``."""
    from app.llm.agents.tools.write_doc import handle

    out = handle(
        {"path": "guide.md", "body": "# Replaced\n", "commit_message": "rewrite"}
    )
    assert out["error"] == "base_sha_required_for_overwrite"


def test_write_doc_overwrite_with_stale_base_sha_merges(repo_with_doc):
    """A stale ``base_sha`` no longer aborts: the new body is 3-way merged
    against the concurrent change. Here the agent rewrites the Alpha section
    while another writer appended a Gamma section — both survive."""
    from app.llm.agents.tools.write_doc import handle
    from app.wiki import git as wiki_git

    base = wiki_git.head_sha_for_path("guide.md")
    # Concurrent writer appends a non-overlapping section.
    wiki_git.commit_file(
        "guide.md",
        "# Guide\n\nAlpha section.\n\nBeta section.\n\nGamma section.\n",
        "concurrent",
        author=None,
    )

    out = handle(
        {
            "path": "guide.md",
            # Agent edited the Alpha line, basing off the pre-Gamma version.
            "body": "# Guide\n\nAlpha section (revised).\n\nBeta section.\n",
            "commit_message": "revise alpha",
            "base_sha": base,
        }
    )
    assert "error" not in out, out
    merged = Path(repo_with_doc.wiki_dir, "guide.md").read_text()
    assert "Alpha section (revised)." in merged  # the agent's change
    assert "Gamma section." in merged  # the concurrent change was preserved


def test_write_doc_overwrite_with_unknown_base_sha_returns_not_found(repo_with_doc):
    from app.llm.agents.tools.write_doc import handle

    out = handle(
        {
            "path": "guide.md",
            "body": "# Replaced\n",
            "commit_message": "rewrite",
            "base_sha": "deadbeef",
        }
    )
    assert out["error"] == "base_sha_not_found"


def test_write_doc_rejects_non_md(repo_with_doc):
    from app.llm.agents.tools.write_doc import handle

    out = handle({"path": "file.txt", "body": "hi", "commit_message": "x"})
    assert "error" in out
    assert ".md" in out["error"]


def test_write_doc_returns_broken_links(repo_with_doc):
    from app.llm.agents.tools.write_doc import handle

    body = "See [missing](nope.md) and [also](still-nope.md).\n"
    out = handle({"path": "intro.md", "body": body, "commit_message": "intro"})
    assert "error" not in out, out
    targets = sorted(b["target"] for b in out["broken_links"])
    assert targets == ["nope.md", "still-nope.md"]


# --------------------------------------------------------------------------- #
# edit_doc                                                                    #
# --------------------------------------------------------------------------- #


def test_edit_doc_simple_replace(repo_with_doc):
    from app.llm.agents.tools.edit_doc import handle

    out = handle(
        {
            "path": "guide.md",
            "old_string": "Alpha section.",
            "new_string": "Alpha section (updated).",
            "commit_message": "tweak",
        }
    )
    assert "error" not in out, out
    new_body = Path(repo_with_doc.wiki_dir, "guide.md").read_text()
    assert "Alpha section (updated)." in new_body
    assert "Beta section." in new_body  # untouched
    assert out["diff"]


def test_edit_doc_no_match_returns_error(repo_with_doc):
    from app.llm.agents.tools.edit_doc import handle

    out = handle(
        {
            "path": "guide.md",
            "old_string": "nonexistent text",
            "new_string": "X",
            "commit_message": "x",
        }
    )
    assert "error" in out
    assert "not found" in out["error"]


def test_edit_doc_ambiguous_returns_error(repo_with_doc):
    from app.wiki import git as wiki_git
    from app.llm.agents.tools.edit_doc import handle

    wiki_git.commit_file("dup.md", "foo\nfoo\nfoo\n", "seed", author=None)

    out = handle(
        {
            "path": "dup.md",
            "old_string": "foo",
            "new_string": "bar",
            "commit_message": "x",
        }
    )
    assert "error" in out
    assert "multiple" in out["error"]


def test_edit_doc_replace_all_works_on_dup(repo_with_doc):
    from app.wiki import git as wiki_git
    from app.llm.agents.tools.edit_doc import handle

    wiki_git.commit_file("dup.md", "foo\nfoo\nfoo\n", "seed", author=None)

    out = handle(
        {
            "path": "dup.md",
            "old_string": "foo",
            "new_string": "bar",
            "replace_all": True,
            "commit_message": "x",
        }
    )
    assert "error" not in out, out
    assert Path(repo_with_doc.wiki_dir, "dup.md").read_text() == "bar\nbar\nbar\n"


def test_edit_doc_rejects_missing_file(repo_with_doc):
    from app.llm.agents.tools.edit_doc import handle

    out = handle(
        {
            "path": "missing.md",
            "old_string": "x",
            "new_string": "y",
            "commit_message": "x",
        }
    )
    assert "error" in out
    assert "not found" in out["error"]


# --------------------------------------------------------------------------- #
# multi_edit                                                                  #
# --------------------------------------------------------------------------- #


def test_multi_edit_applies_all_atomically(repo_with_doc):
    from app.llm.agents.tools.multi_edit import handle

    out = handle(
        {
            "path": "guide.md",
            "edits": [
                {"old_string": "Alpha", "new_string": "First"},
                {"old_string": "Beta", "new_string": "Second"},
            ],
            "commit_message": "rename",
        }
    )
    assert "error" not in out, out
    assert out["applied_count"] == 2
    body = Path(repo_with_doc.wiki_dir, "guide.md").read_text()
    assert "First section." in body
    assert "Second section." in body


def test_multi_edit_aborts_on_any_failure(repo_with_doc):
    """If one edit fails, NO commit happens — file on disk is unchanged."""
    from app.wiki import git as wiki_git
    from app.llm.agents.tools.multi_edit import handle

    head_before = wiki_git._run(["rev-parse", "HEAD"]).stdout.strip()
    body_before = Path(repo_with_doc.wiki_dir, "guide.md").read_text()

    out = handle(
        {
            "path": "guide.md",
            "edits": [
                {"old_string": "Alpha", "new_string": "First"},
                {"old_string": "DOES_NOT_EXIST", "new_string": "X"},
            ],
            "commit_message": "x",
        }
    )
    assert "error" in out
    assert "edit #2" in out["error"]
    head_after = wiki_git._run(["rev-parse", "HEAD"]).stdout.strip()
    assert head_after == head_before  # no commit
    assert Path(repo_with_doc.wiki_dir, "guide.md").read_text() == body_before


def test_multi_edit_chains_through_running_body(repo_with_doc):
    """The 2nd edit can match text the 1st edit produced."""
    from app.llm.agents.tools.multi_edit import handle

    out = handle(
        {
            "path": "guide.md",
            "edits": [
                {"old_string": "Alpha", "new_string": "MID"},
                {"old_string": "MID", "new_string": "Final"},
            ],
            "commit_message": "chain",
        }
    )
    assert "error" not in out, out
    body = Path(repo_with_doc.wiki_dir, "guide.md").read_text()
    assert "Final section." in body
    assert "MID" not in body


def test_multi_edit_rejects_no_op(repo_with_doc):
    from app.llm.agents.tools.multi_edit import handle
    # Each individual edit is a valid no-op-per-step (Alpha->Alpha is a
    # ReplaceNoOp inside wiki_edit), which should bubble up as an error.
    out = handle(
        {
            "path": "guide.md",
            "edits": [{"old_string": "Alpha", "new_string": "Alpha"}],
            "commit_message": "x",
        }
    )
    assert "error" in out
