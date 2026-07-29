"""Tests for ``app/triggers/diff.py``.

Covers the change-view builder (diff for edits, full body fallback for
high-density rewrites and creates), the wiki-snapshot builder (latest
versions of every tracked .md), and the combined payload.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from app.triggers import diff as diff_helper
from app.triggers.diff import _change_entry  # pyright: ignore[reportPrivateUsage]
from app.models.wiki import ChangeKind


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# build_change_view                                                           #
# --------------------------------------------------------------------------- #


def test_change_view_create_returns_full_body():
    out = diff_helper.build_change_view(
        doc_path="new.md", change_kind=ChangeKind.CREATE, before="", after="hello\n"
    )
    assert "Path: new.md" in out
    assert "Kind: create" in out
    assert "(new file" in out
    assert "hello" in out


def test_change_view_edit_returns_unified_diff_when_density_is_low():
    body = "\n".join(f"line {i}" for i in range(50))
    after = body.replace("line 25", "line 25 — updated")
    out = diff_helper.build_change_view(
        doc_path="g.md", change_kind=ChangeKind.EDIT, before=body, after=after
    )
    assert "Kind: edit" in out
    assert "<unified diff>" in out
    assert "line 25 — updated" in out


def test_change_view_falls_back_to_full_body_for_high_density_rewrite():
    out = diff_helper.build_change_view(
        doc_path="g.md",
        change_kind=ChangeKind.EDIT,
        before="old\n",
        after="completely different content\n",
    )
    assert "<unified diff>" not in out
    assert "wholesale rewrite" in out
    assert "BEFORE:" in out
    assert "AFTER:" in out


# --------------------------------------------------------------------------- #
# build_scope_block                                                           #
# --------------------------------------------------------------------------- #


@pytest.fixture
def repo_with_docs(tmp_repo, tmp_config):
    from app.wiki import git as wiki_git

    wiki_git.commit_file("a.md", "# A\n\nalpha body\n", "seed", author=None)
    wiki_git.commit_file("auth/passwords.md", "# Passwords\n", "seed", author=None)
    wiki_git.commit_file("auth/.trigger_x.yaml", "id: x\n", "seed", author=None)
    return tmp_config


def test_scope_block_root_lists_md_files_with_bodies(repo_with_docs):
    snap = diff_helper.build_scope_block("")
    assert "=== SCOPED DOCS (latest version) ===" in snap
    assert "--- a.md" in snap
    assert "alpha body" in snap
    assert "--- auth/passwords.md" in snap
    # YAML trigger files are skipped — the block is .md only
    assert ".trigger_x.yaml" not in snap


def test_scope_block_doc_scope_contains_only_that_doc(repo_with_docs):
    snap = diff_helper.build_scope_block("auth/passwords.md")
    assert "--- auth/passwords.md" in snap
    assert "--- a.md" not in snap
    assert "alpha body" not in snap


def test_scope_block_folder_scope_contains_only_docs_under_it(repo_with_docs):
    snap = diff_helper.build_scope_block("auth")
    assert "--- auth/passwords.md" in snap
    assert "--- a.md" not in snap


def test_scope_block_unknown_scope_says_so(repo_with_docs):
    snap = diff_helper.build_scope_block("nowhere/missing.md")
    assert "(no docs found under this scope)" in snap


# --------------------------------------------------------------------------- #
# build_payload                                                               #
# --------------------------------------------------------------------------- #


def test_payload_combines_scoped_docs_then_change(repo_with_docs):
    payload = diff_helper.build_payload(
        doc_path="a.md",
        change_kind=ChangeKind.EDIT,
        before="# A\n\nalpha body\n",
        after="# A\n\nalpha body updated\n",
        scope_path="a.md",
    )
    scope_idx = payload.index("SCOPED DOCS (latest version)")
    change_idx = payload.index("=== CHANGE ===")
    assert scope_idx < change_idx
    assert "alpha body updated" in payload
    # Scoped: sibling docs stay out of the payload.
    assert "auth/passwords.md" not in payload


def test_payload_accepts_prebuilt_scope_block(repo_with_docs):
    custom = "=== SCOPED DOCS (latest version) ===\n--- stub.md\nstub body\n"
    payload = diff_helper.build_payload(
        doc_path="a.md",
        change_kind=ChangeKind.EDIT,
        before="x\n",
        after="y\n",
        scope_path="a.md",
        scope_block=custom,
    )
    assert payload.startswith(custom)
    assert "stub body" in payload
    # Real wiki not consulted when the block is passed in
    assert "alpha body" not in payload


# --------------------------------------------------------------------------- #
# build_new_file_view / build_new_file_payload                                #
# --------------------------------------------------------------------------- #


def test_new_file_view_shows_path_and_body_no_diff():
    out = diff_helper.build_new_file_view(
        doc_path="projects/foo.md", body="# Foo\n\nstatus: green\n"
    )
    assert "=== NEW FILE ===" in out
    assert "Path: projects/foo.md" in out
    assert "# Foo" in out
    assert "status: green" in out
    # No diff markers — that's the whole point of this view
    assert "<unified diff>" not in out
    assert out.count("+") == 0  # no `+` line prefixes from a diff
    assert "BEFORE:" not in out


def test_new_file_payload_combines_scoped_docs_then_new_file(repo_with_docs):
    payload = diff_helper.build_new_file_payload(
        doc_path="auth/new.md", body="# Foo\n", scope_path="auth"
    )
    scope_idx = payload.index("SCOPED DOCS (latest version)")
    nf_idx = payload.index("=== NEW FILE ===")
    assert scope_idx < nf_idx
    assert "auth/passwords.md" in payload  # scope docs present
    assert "alpha body" not in payload  # out-of-scope doc absent
    assert "Path: auth/new.md" in payload


def test_new_file_payload_accepts_prebuilt_scope_block(repo_with_docs):
    custom = "=== SCOPED DOCS (latest version) ===\n--- stub.md\nstub body\n"
    payload = diff_helper.build_new_file_payload(
        doc_path="auth/new.md", body="# Foo\n", scope_path="auth", scope_block=custom
    )
    assert payload.startswith(custom)
    assert "alpha body" not in payload


# --------------------------------------------------------------------------- #
# _change_entry — per-path classification                                     #
# --------------------------------------------------------------------------- #


def test_change_entry_new_file_shows_full_body():
    out = _change_entry("n.md", "", "# New\n\nbody\n")
    assert out is not None
    assert "(new file)" in out
    assert "body" in out


def test_change_entry_deleted_is_noted():
    out = _change_entry("gone.md", "had content\n", "")
    assert out is not None
    assert "(deleted)" in out
    assert "had content" not in out  # body of a deleted file isn't echoed


def test_change_entry_edit_low_density_is_unified_diff():
    before = "\n".join(f"line {i}" for i in range(50))
    after = before.replace("line 25", "line 25 — updated")
    out = _change_entry("g.md", before, after)
    assert out is not None
    assert "(edited)" in out
    assert "line 25 — updated" in out


def test_change_entry_high_density_rewrite_shows_both_bodies():
    out = _change_entry("g.md", "old\n", "completely different content\n")
    assert out is not None
    assert "(rewritten)" in out
    assert "BEFORE:" in out and "AFTER:" in out


def test_change_entry_no_effective_change_returns_none():
    assert _change_entry("g.md", "same\n", "same\n") is None


# --------------------------------------------------------------------------- #
# build_changes_since                                                         #
# --------------------------------------------------------------------------- #


def test_changes_since_lists_recent_docs_as_new_when_no_prior_commit(repo_with_docs):
    # All commits in the fixture happened "now"; a window opened an hour ago
    # has no commit before it, so every touched .md is a brand-new file.
    since = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    out = diff_helper.build_changes_since(scope_path="", since_iso=since)
    assert "=== CHANGES SINCE LAST CHECK" in out
    assert "--- a.md  (new file)" in out
    assert "alpha body" in out
    # YAML trigger files never appear in the diff block
    assert ".trigger_x.yaml" not in out


def test_changes_since_respects_scope(repo_with_docs):
    since = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    out = diff_helper.build_changes_since(scope_path="auth", since_iso=since)
    assert "auth/passwords.md" in out
    assert "--- a.md" not in out  # outside the scope


def test_changes_since_empty_window(repo_with_docs):
    # A window opening in the future captures nothing.
    since = _iso(datetime.now(timezone.utc) + timedelta(hours=1))
    out = diff_helper.build_changes_since(scope_path="", since_iso=since)
    assert "(no changes in this window)" in out


@contextmanager
def _commit_date(iso: str):
    """Force the git committer/author date of commits made in the block, so
    a time-windowed diff can place the window between commits deterministically."""
    keys = ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE")
    prev = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ[k] = iso
    try:
        yield
    finally:
        for k in keys:
            v = prev[k]
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_changes_since_follows_rename(tmp_repo, tmp_config):
    # A doc renamed mid-window must still show the edit diff, not a spurious
    # "(new file)". The before-state has to be read at the doc's name as of the
    # window-start ref, not its current name.
    from app.wiki import git as wiki_git

    with _commit_date("2026-01-01T00:00:00+00:00"):
        wiki_git.commit_file("old.md", "# T\n\n- [ ] task one\n", "create", author=None)
    with _commit_date("2026-01-01T00:10:00+00:00"):
        wiki_git.move_path("old.md", "new.md", "rename", author=None)
    with _commit_date("2026-01-01T00:20:00+00:00"):
        wiki_git.commit_file("new.md", "# T\n\n- [x] task one\n", "complete", author=None)

    # Window opens after the create, before the rename → before-ref is the
    # create commit (where the doc was still "old.md").
    out = diff_helper.build_changes_since(
        scope_path="new.md", since_iso="2026-01-01T00:05:00+00:00"
    )
    assert "new.md" in out
    assert "(new file)" not in out  # the rename no longer hides the edit
    assert "[x] task one" in out  # the completion transition is visible


# --------------------------------------------------------------------------- #
# build_schedule_payload                                                      #
# --------------------------------------------------------------------------- #


def test_schedule_payload_without_since_has_no_changes_block(repo_with_docs):
    payload = diff_helper.build_schedule_payload(scope_path="", when_iso="2026-06-03T09:00:00+00:00")
    assert "=== SCOPED DOCS (latest version) ===" in payload
    assert "=== SCHEDULED CHECK ===" in payload
    assert "CHANGES SINCE LAST CHECK" not in payload


def test_schedule_payload_with_since_orders_scoped_changes_check(repo_with_docs):
    since = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    payload = diff_helper.build_schedule_payload(
        scope_path="", when_iso="2026-06-03T09:00:00+00:00", since_iso=since
    )
    scope_idx = payload.index("SCOPED DOCS (latest version)")
    chg_idx = payload.index("CHANGES SINCE LAST CHECK")
    chk_idx = payload.index("=== SCHEDULED CHECK ===")
    assert scope_idx < chg_idx < chk_idx


def test_schedule_payload_doc_scope_carries_doc_despite_large_siblings(repo_with_docs, monkeypatch):
    """The scoped doc is always in the payload, no matter how much wiki
    exists alphabetically before it."""
    from app.wiki import git as wiki_git

    wiki_git.commit_file("0_early/huge.md", "# Huge\n" + "x" * 50_000, "seed", author=None)
    monkeypatch.setattr(diff_helper, "_WIKI_TOTAL_BUDGET", 10_000)

    payload = diff_helper.build_schedule_payload(
        scope_path="auth/passwords.md", when_iso="2026-06-03T09:00:00+00:00"
    )
    assert "--- auth/passwords.md" in payload
    assert "0_early/huge.md" not in payload


# --------------------------------------------------------------------------- #
# Schedule change entries: diff-first rendering + dynamic budget              #
# --------------------------------------------------------------------------- #


def test_dense_edit_on_long_page_keeps_tail_changes_visible():
    """The standup-trigger miss: a dense edit to a long page used to render
    as head-truncated BEFORE/AFTER bodies, losing the tail — where the new
    completions lived. The diff-first rule keeps the changed hunks whatever
    their position and density."""
    lines = [f"- [x] item {i} stays unchanged and has padding\n" for i in range(400)]
    before = "".join(lines) + "- [ ] ship the feature\n"
    # Touch ~40% of the lines: the diff is dense (well past the old 0.5
    # density threshold) but still smaller than both bodies together.
    changed = [
        line.replace("stays unchanged", "was reworded") if i % 5 < 2 else line
        for i, line in enumerate(lines)
    ]
    after = "".join(changed) + "- [x] ship the feature\n"
    entry = _change_entry("Individual Notes/Bo TODO.md", before, after, budget=60_000)
    assert entry is not None
    assert "(edited)" in entry
    assert "+- [x] ship the feature" in entry  # the tail change survived


def test_bodies_fallback_only_when_diff_exceeds_both_bodies():
    """Total replacement: every line differs, the diff repeats both bodies
    plus markers — showing the bodies is genuinely smaller."""
    before = "".join(f"alpha {i}\n" for i in range(50))
    after = "".join(f"omega {i}\n" for i in range(50))
    entry = _change_entry("doc.md", before, after, budget=60_000)
    assert entry is not None
    assert "(rewritten)" in entry
    assert "BEFORE:" in entry and "AFTER:" in entry


def test_single_doc_window_gets_the_block_budget(repo_with_docs, monkeypatch):
    """One changed doc in the window → its entry may use the whole block
    budget instead of the fixed per-body cap (no head-truncation of a long
    page's changes)."""
    from datetime import datetime, timedelta, timezone

    from app.wiki import git as wiki_git

    import time

    long_body = "".join(f"- [ ] item {i} with some padding text\n" for i in range(600))
    done_body = long_body.replace("- [ ] item 5 ", "- [x] item 5 ").replace(
        "- [ ] item 599", "- [x] item 599"
    )
    wiki_git.commit_file("todo.md", long_body, "seed", author=None)
    time.sleep(2.2)  # commit timestamps are second-granular
    # Boundary strictly between the two commits: git --since excludes
    # same-second commits, so "now" can race the second commit's timestamp.
    since = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(
        timespec="seconds"
    )
    wiki_git.commit_file("todo.md", done_body, "check items", author=None)

    block = diff_helper.build_changes_since(scope_path="todo.md", since_iso=since)

    assert "(no changes in this window)" not in block
    # The last item's completion — deep past the old 8k cap — is visible.
    assert "+- [x] item 599" in block


def test_rewritten_entry_stays_within_its_budget():
    """The BEFORE/AFTER fallback shares one entry budget between the two
    bodies — a rewritten entry must never weigh ~2x its allowance."""
    before = "".join(f"alpha {i} padding padding padding\n" for i in range(3000))
    after = "".join(f"omega {i} padding padding padding\n" for i in range(3000))
    budget = 10_000
    entry = _change_entry("doc.md", before, after, budget=budget)
    assert entry is not None and "(rewritten)" in entry
    assert len(entry) <= budget + 200  # headers/labels only beyond the shared budget


def test_reverted_paths_do_not_dilute_the_budget(repo_with_docs):
    """A touched-then-reverted page renders nothing and must not shrink the
    genuinely-changed long page's budget share."""
    import time
    from datetime import datetime, timedelta, timezone

    from app.wiki import git as wiki_git

    long_body = "".join(f"- [ ] item {i} with some padding text\n" for i in range(600))
    done_body = long_body.replace("- [ ] item 599", "- [x] item 599")
    wiki_git.commit_file("todo.md", long_body, "seed", author=None)
    wiki_git.commit_file("noise.md", "# noise\noriginal\n", "seed", author=None)
    time.sleep(2.2)
    since = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(
        timespec="seconds"
    )
    # Touch-and-revert noise.md inside the window; really change todo.md.
    wiki_git.commit_file("noise.md", "# noise\nedited\n", "edit", author=None)
    wiki_git.commit_file("noise.md", "# noise\noriginal\n", "revert", author=None)
    wiki_git.commit_file("todo.md", done_body, "check item", author=None)

    block = diff_helper.build_changes_since(scope_path="", since_iso=since)

    assert "noise.md" not in block  # net-zero renders nothing
    assert "+- [x] item 599" in block  # tail change fully visible
