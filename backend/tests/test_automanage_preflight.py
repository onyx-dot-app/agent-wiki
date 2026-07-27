"""Creation preflight — the blocking half of the on-create check.

Agent creates pause (nothing committed) on instant-truth conflicts and the
result carries the suggestion; the agent adapts or explicitly proceeds.
Human/API creates are not gated; the async on-create trigger and the page
banner remain the safety net for everything the gate doesn't see.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from app.llm.agents.tools import dispatch as registry_dispatch
from app.wiki import git as wiki_git
from app.wiki import templates as templates_repo
from app.wiki import utils as wiki_utils
from app.wiki.automanage import preflight
from tests._seed import plumb_commit

_BODY = "# Setup\n\nInstall steps for the app.\n" + "content " * 40
_OTHER = "# Notes\n\nEntirely different material.\n" + "words " * 40


def _fs_case_insensitive() -> bool:
    """True on filesystems (macOS/Windows defaults) where the other casing of
    a path resolves to the same file — there `write_doc` sees the colliding
    page as *existing* and takes the overwrite branch before the create gate;
    the gate is only reachable on case-sensitive filesystems (prod, CI)."""
    with tempfile.TemporaryDirectory() as d:
        probe = os.path.join(d, "CaseProbe")
        open(probe, "w").write("x")
        return os.path.exists(os.path.join(d, "caseprobe"))


_needs_case_sensitive_fs = pytest.mark.skipif(
    _fs_case_insensitive(),
    reason="case-colliding create paths resolve to the existing file here",
)


def _write(path: str, body: str, **extra) -> dict:
    return registry_dispatch(
        "write_doc",
        {"path": path, "body": body, "commit_message": "create", **extra},
    )


# --------------------------------------------------------------------------- #
# check_creation                                                              #
# --------------------------------------------------------------------------- #


def test_duplicate_body_conflicts(tmp_repo):
    wiki_git.commit_file("team/original.md", _BODY, "seed", author=None)
    (c,) = preflight.check_creation("team/copy.md", _BODY)
    assert c.kind == "duplicate"
    assert c.existing_path == "team/original.md"


def test_case_collision_conflicts_regardless_of_body(tmp_repo):
    wiki_git.commit_file("docs/Setup.md", _BODY, "seed", author=None)
    (c,) = preflight.check_creation("docs/setup.md", _OTHER)
    assert c.kind == "case_collision"
    assert c.existing_path == "docs/Setup.md"


def test_clean_create_has_no_conflicts(tmp_repo):
    wiki_git.commit_file("team/original.md", _BODY, "seed", author=None)
    assert preflight.check_creation("team/unique.md", _OTHER) == []


def test_tiny_body_never_counts_as_duplicate(tmp_repo):
    wiki_git.commit_file("t/a.md", "# a\n", "seed", author=None)
    assert preflight.check_creation("t/b.md", "# a\n") == []


def test_template_instance_is_not_a_duplicate(tmp_repo):
    """Creating from a template is legitimate even when another untouched
    instance exists — template-echo owns skeletons on its own clock."""
    tpl_body = "# Weekly Notes\n\nFill in your updates here.\n" + "section " * 30
    row = templates_repo.create(
        name="Weekly",
        body=tpl_body,
        description=None,
        system_prompt=None,
        created_by_user_id=None,
    )
    assert row["id"]
    wiki_git.commit_file("notes/week1.md", tpl_body, "seed instance", author=None)
    assert preflight.check_creation("notes/week2.md", tpl_body) == []


# --------------------------------------------------------------------------- #
# write_doc pause protocol                                                    #
# --------------------------------------------------------------------------- #


def test_create_pauses_on_duplicate_and_commits_nothing(tmp_repo):
    wiki_git.commit_file("team/original.md", _BODY, "seed", author=None)

    out = _write("team/copy.md", _BODY)

    assert out["paused"] is True
    (conflict,) = out["conflicts"]
    assert conflict["kind"] == "duplicate"
    assert conflict["existing_path"] == "team/original.md"
    assert "proceed_despite_conflict" in out["message"]
    assert not wiki_utils.file_exists("team/copy.md")  # nothing committed


@_needs_case_sensitive_fs
def test_create_pauses_on_case_collision(tmp_repo):
    wiki_git.commit_file("docs/Setup.md", _BODY, "seed", author=None)
    out = _write("docs/setup.md", _OTHER)
    assert out["paused"] is True
    assert out["conflicts"][0]["kind"] == "case_collision"
    assert not wiki_utils.file_exists("docs/setup.md")


def test_proceed_despite_conflict_creates(tmp_repo):
    wiki_git.commit_file("team/original.md", _BODY, "seed", author=None)
    out = _write("team/copy.md", _BODY, proceed_despite_conflict=True)
    assert out.get("created") is True
    assert wiki_utils.file_exists("team/copy.md")


def test_clean_create_is_untouched_by_the_gate(tmp_repo):
    out = _write("team/fresh.md", _BODY)
    assert out.get("created") is True
    assert "paused" not in out


def test_overwrite_path_never_pays_the_gate(tmp_repo):
    """Edits can't introduce a collision and dup-on-edit is the sweep's
    business — only creates are checked."""
    wiki_git.commit_file("team/original.md", _BODY, "seed", author=None)
    sha = wiki_git.commit_file("team/page.md", _OTHER, "seed", author=None)
    out = _write("team/page.md", _BODY, base_sha=sha)
    assert out.get("created") is False  # became a duplicate, but committed
    assert "paused" not in out


def test_preflight_failure_fails_open(tmp_repo, monkeypatch):
    """A broken check must never block a write — the async trigger and the
    banner are the safety net."""
    wiki_git.commit_file("team/original.md", _BODY, "seed", author=None)
    monkeypatch.setattr(
        preflight, "check_creation", lambda *a: (_ for _ in ()).throw(RuntimeError)
    )
    out = _write("team/copy.md", _BODY)
    assert out.get("created") is True


@_needs_case_sensitive_fs
def test_case_collision_seeded_via_plumbing_pauses(tmp_repo):
    """The collision partner staged the plumbing way (case-insensitive dev
    filesystems), matching how the wiki actually gets into this state."""
    wiki_git.commit_file("n/Page.md", _BODY, "seed", author=None)
    plumb_commit("n/page.md", _OTHER)
    out = _write("n/PAGE.md", "# distinct\n" + "x " * 80)
    assert out["paused"] is True
    kinds = {c["kind"] for c in out["conflicts"]}
    assert kinds == {"case_collision"}
    assert len(out["conflicts"]) == 2  # both existing casings named
