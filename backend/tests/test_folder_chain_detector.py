"""Single-child folder-chain flattening detector.

Detection is pure over the scope's tracked-file list (age gate aside): a run
of folders each holding exactly one subfolder flattens by moving the tail's
pages up into the chain head, structure below the tail preserved. Wrapper
``.gitkeep`` markers stay behind for the empty-folder detector.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.wiki import git as wiki_git
from app.wiki.automanage.detectors.base import Scope, TriggerKind
from app.wiki.automanage.detectors.folder_chain import _FolderChainDetector

_BODY = "# Page\n\ncontent\n"


@pytest.fixture
def repo(tmp_repo):
    return tmp_repo


def _detect(*paths: str, trigger: TriggerKind = TriggerKind.SWEEP):
    # min_age_days=0 so tests exercise structure, not the grace window.
    det = _FolderChainDetector(min_age_days=0)
    return det.detect(Scope(trigger=trigger, paths=tuple(paths)))


def _seed(*paths: str) -> None:
    for p in paths:
        body = "" if p.endswith(".gitkeep") else _BODY
        wiki_git.commit_file(p, body, "seed", author=None)


def test_two_level_chain_flattens_into_head(repo):
    _seed("wrap/.gitkeep", "wrap/inner/a.md", "wrap/inner/b.md", "other/x.md")

    drafts = _detect(
        "wrap/.gitkeep", "wrap/inner/a.md", "wrap/inner/b.md", "other/x.md"
    )

    assert len(drafts) == 1
    d = drafts[0]
    assert d.op.value == "move"
    # Chain folders first (head → tail), then the pages, index-aligned dests.
    assert d.source_paths == ["wrap", "wrap/inner", "wrap/inner/a.md", "wrap/inner/b.md"]
    assert d.target_paths == ["wrap/a.md", "wrap/b.md"]
    assert d.auto_approvable is False  # surviving name is naming judgment
    assert d.instruction and "move_page" in d.instruction


def test_multi_level_chain_collapses_to_one_proposal(repo):
    paths = ("a/b/c/x.md", "a/b/c/sub/y.md")
    _seed(*paths)

    drafts = _detect(*paths)

    assert len(drafts) == 1
    d = drafts[0]
    assert d.source_paths == ["a", "a/b", "a/b/c", "a/b/c/sub/y.md", "a/b/c/x.md"]
    # Structure below the tail is preserved.
    assert d.target_paths == ["a/sub/y.md", "a/x.md"]


def test_branching_folder_is_not_a_chain(repo):
    paths = ("a/b/x.md", "a/c/y.md", "a/z.md")
    _seed(*paths)
    assert _detect(*paths) == []


def test_empty_chain_is_left_to_the_empty_folder_detector(repo):
    paths = ("a/b/.gitkeep",)
    _seed(*paths)
    assert _detect(*paths) == []


def test_non_page_content_under_the_tail_skips_the_chain(repo):
    paths = ("a/b/x.md", "a/b/.trigger_t1.yaml")
    _seed(*paths)
    assert _detect(*paths) == []


def test_occupied_destination_skips_the_chain(repo):
    # `A/x.md` differs from the destination `a/x.md` only by case — flattening
    # would manufacture a case collision, so the chain is skipped.
    paths = ("a/b/x.md", "A/x.md")
    assert _detect(*paths) == []


def test_nested_chain_inside_an_accepted_tail_is_skipped(repo):
    # Outer chain a→b (tail b holds a page and the inner chain c→d). One
    # proposal: the outer flatten; the recreated inner chain waits for a
    # later pass.
    paths = ("a/b/x.md", "a/b/c/d/y.md")
    _seed(*paths)

    drafts = _detect(*paths)

    assert len(drafts) == 1
    assert drafts[0].source_paths[:2] == ["a", "a/b"]
    assert drafts[0].target_paths == ["a/c/d/y.md", "a/x.md"]


def test_young_chain_is_left_alone(repo):
    paths = ("wrap/inner/a.md",)
    _seed(*paths)
    det = _FolderChainDetector(min_age_days=7)  # seeded moments ago
    assert det.detect(Scope(trigger=TriggerKind.SWEEP, paths=paths)) == []


def test_on_create_trigger_is_not_applicable(repo):
    paths = ("wrap/inner/a.md",)
    _seed(*paths)
    assert _detect(*paths, trigger=TriggerKind.ON_CREATE) == []


def _proposal(drafts: list[Any]) -> dict[str, Any]:
    d = drafts[0]
    return {"source_paths": d.source_paths, "target_paths": d.target_paths}


def test_validate_survives_a_body_edit(repo):
    _seed("wrap/inner/a.md")
    p = _proposal(_detect("wrap/inner/a.md"))

    wiki_git.commit_file("wrap/inner/a.md", "# Edited\n", "edit", author=None)

    # Premise-based: an edit doesn't break "the chain is redundant".
    assert _FolderChainDetector().validate(p) is None


def test_validate_stales_when_a_page_is_added_under_the_tail(repo):
    _seed("wrap/inner/a.md")
    p = _proposal(_detect("wrap/inner/a.md"))

    _seed("wrap/inner/new.md")  # would be stranded half-flattened

    reason = _FolderChainDetector().validate(p)
    assert reason is not None and "changed" in reason


def test_validate_stales_when_the_chain_gains_a_sibling(repo):
    _seed("wrap/inner/a.md")
    p = _proposal(_detect("wrap/inner/a.md"))

    _seed("wrap/direct.md")  # head now has content of its own — no chain

    reason = _FolderChainDetector().validate(p)
    assert reason is not None and "no longer" in reason


def test_validate_stales_when_a_destination_is_occupied(repo):
    # A file appearing at the destination inside the head also breaks the
    # chain premise (the head gains direct content), so the chain check fires
    # first — the destination check is the backstop for anything that slips
    # past it. Exercise it directly with an occupied target elsewhere.
    _seed("wrap/inner/a.md", "unrelated/x.md")
    p = _proposal(_detect("wrap/inner/a.md", "unrelated/x.md"))
    p["target_paths"] = ["unrelated/X.md"]  # occupied, case-insensitively

    reason = _FolderChainDetector().validate(p)
    assert reason is not None and "destination" in reason
