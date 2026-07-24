"""Stub/placeholder-page detector.

Content measurement is pure (scaffolding stripped, whitespace collapsed);
detection adds the template-echo precedence skip and the quiet-window age
gate; validate re-checks the still-a-stub premise at execution time.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.wiki import git as wiki_git
from app.wiki import templates
from app.wiki.automanage.detectors.base import Scope, TriggerKind
from app.wiki.automanage.detectors.stub_page import (
    _StubPageDetector,
    content_bytes,
)


@pytest.fixture
def repo(tmp_repo, tmp_db):
    return tmp_repo


def _detect(*paths: str, trigger: TriggerKind = TriggerKind.SWEEP):
    det = _StubPageDetector(min_age_days=0)
    return det.detect(Scope(trigger=trigger, paths=tuple(paths)))


def _seed(path: str, body: str) -> None:
    wiki_git.commit_file(path, body, "seed", author=None)


def test_content_bytes_strips_scaffolding():
    assert content_bytes("# Title\n") == 0
    assert content_bytes("# Title\n\n---\n\n## Section\n") == 0
    assert content_bytes("# Notes\n\nTODO\n") == len(b"TODO")
    # Whitespace runs collapse; real prose counts byte-for-byte.
    assert content_bytes("hello   world") == len(b"hello world")


def test_title_only_page_emits_removal(repo):
    _seed("proj/notes.md", "# Notes\n")
    drafts = _detect("proj/notes.md")

    assert len(drafts) == 1
    d = drafts[0]
    assert d.op.value == "delete_page"
    assert d.source_paths == ["proj/notes.md"]
    assert d.target_paths == []
    assert d.auto_approvable is False  # smallness is evidence, not proof
    assert "trash_page" in (d.instruction or "")


def test_placeholder_text_below_floor_emits(repo):
    _seed("proj/soon.md", "# Roadmap\n\nComing soon.\n")
    assert len(_detect("proj/soon.md")) == 1


def test_one_real_sentence_clears_the_floor(repo):
    _seed(
        "proj/real.md",
        "# Decision\n\nWe picked Postgres over DynamoDB for the queue store.\n",
    )
    assert _detect("proj/real.md") == []


def test_template_identical_page_is_left_to_template_echo(repo):
    body = "# <Project name>\n\n**Owner:** <name>\n"
    templates.create(
        name="Project",
        body=body,
        description=None,
        system_prompt=None,
        created_by_user_id=None,
    )
    _seed("proj/skeleton.md", body)
    # Small enough to be a stub, but byte-identical to a template — the
    # template-echo detector owns it (its proposal names the template).
    assert _detect("proj/skeleton.md") == []


def test_recent_stub_is_left_alone(repo):
    _seed("proj/new.md", "# New\n")
    det = _StubPageDetector(min_age_days=7)  # seeded moments ago
    assert det.detect(Scope(trigger=TriggerKind.SWEEP, paths=("proj/new.md",))) == []


def test_only_sweep_trigger_applies(repo):
    _seed("proj/notes.md", "# Notes\n")
    assert _detect("proj/notes.md", trigger=TriggerKind.ON_WRITE) == []
    assert _detect("proj/notes.md", trigger=TriggerKind.ON_CREATE) == []


def _proposal(drafts: list[Any]) -> dict[str, Any]:
    return {"source_paths": drafts[0].source_paths}


def test_validate_stales_once_content_lands(repo):
    _seed("proj/notes.md", "# Notes\n")
    p = _proposal(_detect("proj/notes.md"))

    _seed("proj/notes.md", "# Notes\n\nReal decisions live here now, in detail.\n")

    reason = _StubPageDetector().validate(p)
    assert reason is not None and "content" in reason


def test_validate_holds_across_scaffolding_shuffles(repo):
    _seed("proj/notes.md", "# Notes\n")
    p = _proposal(_detect("proj/notes.md"))

    _seed("proj/notes.md", "## Notes\n\n---\n\n### Later\n")  # still no content

    # Still a stub, but the edit restarted the quiet window — an
    # age-agnostic validator would trash a page someone touched today.
    reason = _StubPageDetector().validate(p)
    assert reason is not None and "quiet window" in reason
    # With the window elapsed (age gate disabled), the premise holds.
    assert _StubPageDetector(min_age_days=0).validate(p) is None


def test_validate_stales_when_the_page_is_gone(repo):
    _seed("proj/notes.md", "# Notes\n")
    p = _proposal(_detect("proj/notes.md"))

    wiki_git.delete_path("proj/notes.md", "removed", author=None)

    reason = _StubPageDetector().validate(p)
    assert reason is not None and "exists" in reason
