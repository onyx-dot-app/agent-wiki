"""Empty-folder detector — the first Wiki Auto Management detection technique.

Pure helpers (folder enumeration + age gate) are tested directly; ``detect``
runs against a real tmp wiki repo with the age gate opened so we don't have to
forge commit dates.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.wiki.automanage.detectors import empty_folder as ef
from app.wiki.automanage.detectors.base import Scope, TriggerKind
from app.wiki.change_proposals import ProposalOp


# --------------------------------------------------------------------------- #
# _maximal_empty_folders (pure)                                               #
# --------------------------------------------------------------------------- #


def test_folder_with_only_gitkeep_is_empty():
    assert ef._maximal_empty_folders(["ops/.gitkeep"]) == ["ops"]


def test_folder_with_a_page_is_not_empty():
    assert ef._maximal_empty_folders(["ops/.gitkeep", "ops/runbook.md"]) == []


def test_nested_empties_collapse_to_maximal_ancestor():
    # ops/ and ops/old/ are both empty; only the shallowest is proposed.
    paths = ["ops/.gitkeep", "ops/old/.gitkeep"]
    assert ef._maximal_empty_folders(paths) == ["ops"]


def test_empty_sibling_beside_populated_sibling():
    paths = ["team/live/plan.md", "team/live/.gitkeep", "team/archive/.gitkeep"]
    assert ef._maximal_empty_folders(paths) == ["team/archive"]


def test_folder_holding_a_trigger_file_is_not_empty():
    # A lingering folder-scoped trigger keeps the folder alive.
    paths = ["ops/.gitkeep", "ops/.trigger_7.yaml"]
    assert ef._maximal_empty_folders(paths) == []


def test_root_is_never_proposed():
    assert ef._maximal_empty_folders([".gitkeep"]) == []


# --------------------------------------------------------------------------- #
# _empty_long_enough (pure)                                                   #
# --------------------------------------------------------------------------- #


def test_age_gate_blocks_recent_and_allows_old():
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    recent = "2026-07-15T12:00:00+00:00"  # ~0.5 days
    old = "2026-07-10T12:00:00+00:00"  # ~6 days
    assert ef._empty_long_enough(recent, now, min_age_days=2) is False
    assert ef._empty_long_enough(old, now, min_age_days=2) is True


def test_age_gate_fails_closed_on_garbage():
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    assert ef._empty_long_enough("not-a-date", now, min_age_days=2) is False


# --------------------------------------------------------------------------- #
# detect (real tmp repo)                                                      #
# --------------------------------------------------------------------------- #


@pytest.fixture
def repo(tmp_repo, tmp_config):
    from app.wiki import git as wiki_git

    # A populated folder, an empty folder, and a page later removed.
    wiki_git.commit_file("team/plan.md", "# Plan\n", "seed", author=None)
    wiki_git.commit_file("archive/.gitkeep", "", "create folder archive", author=None)
    wiki_git.commit_file("old/notes.md", "# Notes\n", "seed", author=None)
    wiki_git.commit_file("old/.gitkeep", "", "keep old", author=None)
    wiki_git.delete_path("old/notes.md", "remove notes", author=None)
    return tmp_config


def _paths(cfg) -> list[str]:
    from app.wiki import git as wiki_git

    return wiki_git.list_paths()


def test_detect_emits_delete_for_empty_folders(repo):
    det = ef._EmptyFolderDetector(min_age_days=0)  # ignore age for this case
    scope = Scope(trigger=TriggerKind.SWEEP, paths=_paths(repo))
    drafts = det.detect(scope)
    folders = {d.source_paths[0] for d in drafts}
    # `archive` (created empty) and `old` (emptied by the delete) qualify;
    # `team` holds a page and must not.
    assert folders == {"archive", "old"}
    for d in drafts:
        assert d.op is ProposalOp.DELETE_EMPTY_FOLDER
        assert d.target_paths == []


def test_detect_respects_age_gate(repo):
    # With a 365-day floor, the just-committed empties are too young.
    det = ef._EmptyFolderDetector(min_age_days=365)
    scope = Scope(trigger=TriggerKind.SWEEP, paths=_paths(repo))
    assert det.detect(scope) == []


def test_detect_skips_on_create_trigger(repo):
    det = ef._EmptyFolderDetector(min_age_days=0)
    scope = Scope(trigger=TriggerKind.ON_CREATE, paths=_paths(repo))
    assert det.detect(scope) == []
