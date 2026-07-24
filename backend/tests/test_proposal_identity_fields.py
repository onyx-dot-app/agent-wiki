"""The identity fields on change_proposals — storage plumbing only.

The dedup component that computes and consumes these lands separately;
this pins that create() persists them and defaults are sane.
"""
from __future__ import annotations

import pytest

from app.wiki import git as wiki_git
from app.wiki.change_proposals import ProposalCreatedVia, ProposalOp, create


@pytest.fixture
def repo(tmp_repo, tmp_db):
    wiki_git.commit_file("docs/a.md", "# A\n", "seed", author=None)
    return tmp_repo


def test_identity_fields_roundtrip(repo):
    row = create(
        op=ProposalOp.DELETE_PAGE,
        source_paths=["docs/a.md"],
        target_paths=[],
        base_shas={"docs/a.md": "0" * 40},
        summary="remove stub",
        created_via=ProposalCreatedVia.SWEEP,
        detector="stub_page",
        dedup_key="stub_page|delete_page|id:abc|",
        doc_ids={"abc": "docs/a.md"},
    )
    assert row["dedup_key"] == "stub_page|delete_page|id:abc|"
    assert row["doc_ids"] == {"abc": "docs/a.md"}
    assert row["revive_count"] == 0
    assert row["last_emitted_at"] is not None


def test_identity_fields_optional(repo):
    row = create(
        op=ProposalOp.DELETE_PAGE,
        source_paths=["docs/a.md"],
        target_paths=[],
        base_shas={"docs/a.md": "0" * 40},
        summary="remove stub",
        created_via=ProposalCreatedVia.SWEEP,
    )
    assert row["dedup_key"] is None
    assert row["doc_ids"] is None
