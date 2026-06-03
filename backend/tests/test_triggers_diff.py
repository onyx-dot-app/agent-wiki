"""Tests for ``app/triggers/diff.py``.

Covers the change-view builder (diff for edits, full body fallback for
high-density rewrites and creates), the wiki-snapshot builder (latest
versions of every tracked .md), and the combined payload.
"""
from __future__ import annotations

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
# build_wiki_snapshot                                                         #
# --------------------------------------------------------------------------- #


@pytest.fixture
def repo_with_docs(tmp_repo, tmp_config):
    from app.wiki import git as wiki_git

    wiki_git.commit_file("a.md", "# A\n\nalpha body\n", "seed", author=None)
    wiki_git.commit_file("auth/passwords.md", "# Passwords\n", "seed", author=None)
    wiki_git.commit_file("auth/.trigger_x.yaml", "id: x\n", "seed", author=None)
    return tmp_config


def test_wiki_snapshot_lists_md_files_with_bodies(repo_with_docs):
    snap = diff_helper.build_wiki_snapshot()
    assert "=== WIKI (latest version) ===" in snap
    assert "--- a.md" in snap
    assert "alpha body" in snap
    assert "--- auth/passwords.md" in snap
    # YAML trigger files are skipped — snapshot is .md only
    assert ".trigger_x.yaml" not in snap


# --------------------------------------------------------------------------- #
# build_payload                                                               #
# --------------------------------------------------------------------------- #


def test_payload_combines_snapshot_then_change(repo_with_docs):
    payload = diff_helper.build_payload(
        doc_path="a.md",
        change_kind=ChangeKind.EDIT,
        before="# A\n\nalpha body\n",
        after="# A\n\nalpha body updated\n",
    )
    snap_idx = payload.index("WIKI (latest version)")
    change_idx = payload.index("=== CHANGE ===")
    assert snap_idx < change_idx
    assert "alpha body updated" in payload


def test_payload_accepts_prebuilt_snapshot(repo_with_docs):
    custom = "=== WIKI (latest version) ===\n--- stub.md\nstub body\n"
    payload = diff_helper.build_payload(
        doc_path="a.md",
        change_kind=ChangeKind.EDIT,
        before="x\n",
        after="y\n",
        wiki_snapshot=custom,
    )
    assert payload.startswith(custom)
    assert "stub body" in payload
    # Real wiki not consulted when snapshot is passed in
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


def test_new_file_payload_combines_snapshot_then_new_file(repo_with_docs):
    payload = diff_helper.build_new_file_payload(
        doc_path="projects/foo.md", body="# Foo\n"
    )
    snap_idx = payload.index("WIKI (latest version)")
    nf_idx = payload.index("=== NEW FILE ===")
    assert snap_idx < nf_idx
    assert "alpha body" in payload  # real snapshot was used
    assert "Path: projects/foo.md" in payload


def test_new_file_payload_accepts_prebuilt_snapshot(repo_with_docs):
    custom = "=== WIKI (latest version) ===\n--- stub.md\nstub body\n"
    payload = diff_helper.build_new_file_payload(
        doc_path="projects/foo.md", body="# Foo\n", wiki_snapshot=custom
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


# --------------------------------------------------------------------------- #
# build_schedule_payload                                                      #
# --------------------------------------------------------------------------- #


def test_schedule_payload_without_since_has_no_changes_block(repo_with_docs):
    payload = diff_helper.build_schedule_payload(scope_path="", when_iso="2026-06-03T09:00:00+00:00")
    assert "=== WIKI (latest version) ===" in payload
    assert "=== SCHEDULED CHECK ===" in payload
    assert "CHANGES SINCE LAST CHECK" not in payload


def test_schedule_payload_with_since_orders_snapshot_changes_check(repo_with_docs):
    since = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    payload = diff_helper.build_schedule_payload(
        scope_path="", when_iso="2026-06-03T09:00:00+00:00", since_iso=since
    )
    snap_idx = payload.index("WIKI (latest version)")
    chg_idx = payload.index("CHANGES SINCE LAST CHECK")
    chk_idx = payload.index("=== SCHEDULED CHECK ===")
    assert snap_idx < chg_idx < chk_idx
