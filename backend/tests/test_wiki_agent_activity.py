"""Unit tests for ``app/wiki/agent_activity.py``.

Covers three concerns that share a module but test independently:

1. Pure frontmatter parse / split / render — no DB needed.
2. The tamper guard (``assert_frontmatter_unchanged``) — pure.
3. Registry CRUD + the DB-backed ``replace_frontmatter`` round-trip.

The first two tiers run on no fixture; the third pulls in ``tmp_db``
plus a seeded ``User`` row so the join in ``_select_with_owner`` has
something to attach to.
"""
from __future__ import annotations

from datetime import timedelta

import pytest


# --------------------------------------------------------------------------- #
# Tier 1 — split_frontmatter                                                  #
# --------------------------------------------------------------------------- #


def test_split_frontmatter_returns_none_when_no_fence():
    from app.wiki.agent_activity import split_frontmatter

    fm, rest = split_frontmatter("# Heading\n\nbody\n")
    assert fm is None
    assert rest == "# Heading\n\nbody\n"


def test_split_frontmatter_returns_none_when_fence_not_at_byte_zero():
    """Leading whitespace before the fence disqualifies it — the parser
    only recognizes byte-0 ``---\\n``."""
    from app.wiki.agent_activity import split_frontmatter

    fm, _ = split_frontmatter("\n---\nfoo: 1\n---\nbody\n")
    assert fm is None


def test_split_frontmatter_extracts_inner_text_and_trailing_body():
    from app.wiki.agent_activity import split_frontmatter

    fm, rest = split_frontmatter("---\nfoo: 1\nbar: two\n---\n# Body\n")
    assert fm == "foo: 1\nbar: two"
    assert rest == "# Body\n"


def test_split_frontmatter_handles_eof_terminator():
    """Doc that ends with ``\\n---`` (no trailing newline) still splits."""
    from app.wiki.agent_activity import split_frontmatter

    fm, rest = split_frontmatter("---\nfoo: 1\n---")
    assert fm == "foo: 1"
    assert rest == ""


def test_split_frontmatter_returns_none_when_unterminated():
    from app.wiki.agent_activity import split_frontmatter

    fm, _ = split_frontmatter("---\nfoo: 1\nno end fence here\n")
    assert fm is None


# --------------------------------------------------------------------------- #
# Tier 2 — tamper guard                                                       #
# --------------------------------------------------------------------------- #


def _wrap_agents(yaml_block: str) -> str:
    return f"---\n{yaml_block}\n---\nbody\n"


def test_tamper_guard_passes_when_neither_side_has_agents():
    from app.wiki.agent_activity import assert_frontmatter_unchanged

    # No frontmatter on either side — vacuously equal.
    assert_frontmatter_unchanged(incoming_body="just body", current_disk_body="just body")
    # Frontmatter without ``agents:`` is fine to differ.
    assert_frontmatter_unchanged(
        incoming_body=_wrap_agents("title: a"),
        current_disk_body=_wrap_agents("title: b"),
    )


def test_tamper_guard_passes_on_byte_identical_agents_block():
    from app.wiki.agent_activity import assert_frontmatter_unchanged

    block = (
        "agents:\n"
        "  - owner: u\n"
        "    agent: w\n"
        "    activity: read\n"
        "    description: N/A\n"
        "    expires_at: '2099-01-01T00:00:00+00:00'\n"
    )
    assert_frontmatter_unchanged(
        incoming_body=_wrap_agents(block),
        current_disk_body=_wrap_agents(block),
    )


def test_tamper_guard_passes_on_reordered_keys():
    """Semantic equality: the same entries with keys in a different
    order shouldn't trip the guard. Otherwise an editor's autoformat
    would falsely accuse the agent.
    """
    from app.wiki.agent_activity import assert_frontmatter_unchanged

    incoming = (
        "agents:\n"
        "  - activity: read\n"
        "    agent: w\n"
        "    owner: u\n"
        "    description: N/A\n"
        "    expires_at: '2099-01-01T00:00:00+00:00'\n"
    )
    current = (
        "agents:\n"
        "  - owner: u\n"
        "    agent: w\n"
        "    activity: read\n"
        "    description: N/A\n"
        "    expires_at: '2099-01-01T00:00:00+00:00'\n"
    )
    assert_frontmatter_unchanged(
        incoming_body=_wrap_agents(incoming),
        current_disk_body=_wrap_agents(current),
    )


def test_tamper_guard_rejects_added_entry():
    from app.wiki.agent_activity import (
        FrontmatterTamperedError, assert_frontmatter_unchanged,
    )

    current = (
        "agents:\n"
        "  - owner: u\n"
        "    agent: w\n"
        "    activity: read\n"
        "    description: N/A\n"
        "    expires_at: '2099-01-01T00:00:00+00:00'\n"
    )
    incoming = current + (
        "  - owner: attacker\n"
        "    agent: m\n"
        "    activity: read\n"
        "    description: spoofed\n"
        "    expires_at: '2099-01-01T00:00:00+00:00'\n"
    )
    with pytest.raises(FrontmatterTamperedError):
        assert_frontmatter_unchanged(
            incoming_body=_wrap_agents(incoming),
            current_disk_body=_wrap_agents(current),
        )


def test_tamper_guard_rejects_modified_field():
    from app.wiki.agent_activity import (
        FrontmatterTamperedError, assert_frontmatter_unchanged,
    )

    current = (
        "agents:\n"
        "  - owner: u\n"
        "    agent: w\n"
        "    activity: read\n"
        "    description: N/A\n"
        "    expires_at: '2099-01-01T00:00:00+00:00'\n"
    )
    incoming = current.replace("expires_at: '2099", "expires_at: '2199")
    with pytest.raises(FrontmatterTamperedError):
        assert_frontmatter_unchanged(
            incoming_body=_wrap_agents(incoming),
            current_disk_body=_wrap_agents(current),
        )


def test_tamper_guard_rejects_removed_block():
    from app.wiki.agent_activity import (
        FrontmatterTamperedError, assert_frontmatter_unchanged,
    )

    current = _wrap_agents(
        "agents:\n"
        "  - owner: u\n"
        "    agent: w\n"
        "    activity: read\n"
        "    description: N/A\n"
        "    expires_at: '2099-01-01T00:00:00+00:00'\n"
    )
    incoming = "# Just body, no frontmatter\n"
    with pytest.raises(FrontmatterTamperedError):
        assert_frontmatter_unchanged(
            incoming_body=incoming, current_disk_body=current,
        )


def test_tamper_guard_rejects_invalid_yaml():
    from app.wiki.agent_activity import (
        FrontmatterTamperedError, assert_frontmatter_unchanged,
    )

    with pytest.raises(FrontmatterTamperedError):
        assert_frontmatter_unchanged(
            incoming_body="---\n: : : not yaml\n---\nbody\n",
            current_disk_body="body\n",
        )


def test_tamper_guard_rejects_non_mapping_top_level():
    """Frontmatter that parses to a list (not a dict) is malformed."""
    from app.wiki.agent_activity import (
        FrontmatterTamperedError, assert_frontmatter_unchanged,
    )

    with pytest.raises(FrontmatterTamperedError):
        assert_frontmatter_unchanged(
            incoming_body="---\n- one\n- two\n---\nbody\n",
            current_disk_body="body\n",
        )


def test_tamper_guard_rejects_non_list_agents_field():
    from app.wiki.agent_activity import (
        FrontmatterTamperedError, assert_frontmatter_unchanged,
    )

    with pytest.raises(FrontmatterTamperedError):
        assert_frontmatter_unchanged(
            incoming_body="---\nagents: not-a-list\n---\nbody\n",
            current_disk_body="body\n",
        )


# --------------------------------------------------------------------------- #
# Tier 3 — registry CRUD + replace_frontmatter round-trip                     #
# --------------------------------------------------------------------------- #


@pytest.fixture
def seeded_user(tmp_db):
    """A real ``User`` row so the join in ``_select_with_owner`` resolves."""
    from app.auth import users as users_repo
    return users_repo.create(email="agent-user@x.com", password="hunter2-x", name="Author")


def test_upsert_creates_then_renews(seeded_user):
    from app.wiki import agent_activity

    e1 = agent_activity.upsert_activity(
        user_id=seeded_user, agent_name=None, doc_path="guide.md",
        activity="read", description=None,
    )
    e2 = agent_activity.upsert_activity(
        user_id=seeded_user, agent_name=None, doc_path="guide.md",
        activity="read", description=None,
        ttl=timedelta(hours=48),
    )
    # Same natural key ⇒ second call slides expires_at, doesn't insert.
    rows = agent_activity.list_for_doc("guide.md")
    assert len(rows) == 1
    assert rows[0].expires_at == e2
    assert e2 > e1  # 48h horizon is later than 24h


def test_upsert_distinct_agent_names_are_separate_rows(seeded_user):
    """``agent_name=None`` and ``agent_name="x"`` are distinct via NULLS NOT DISTINCT."""
    from app.wiki import agent_activity

    agent_activity.upsert_activity(
        user_id=seeded_user, agent_name=None, doc_path="guide.md",
        activity="read", description=None,
    )
    agent_activity.upsert_activity(
        user_id=seeded_user, agent_name="alpha", doc_path="guide.md",
        activity="read", description=None,
    )
    rows = agent_activity.list_for_doc("guide.md")
    assert len(rows) == 2
    names = sorted([r.agent_name for r in rows], key=lambda x: (x is not None, x or ""))
    assert names == [None, "alpha"]


def test_upsert_rejects_unknown_activity(seeded_user):
    from app.wiki import agent_activity

    with pytest.raises(ValueError):
        agent_activity.upsert_activity(
            user_id=seeded_user, agent_name=None, doc_path="x.md",
            activity="deleted", description=None,
        )


def test_list_for_doc_filters_expired(seeded_user):
    from app.wiki import agent_activity

    agent_activity.upsert_activity(
        user_id=seeded_user, agent_name="live", doc_path="x.md",
        activity="read", description=None, ttl=timedelta(hours=1),
    )
    # An already-expired row with TTL in the past — list_for_doc must hide it.
    agent_activity.upsert_activity(
        user_id=seeded_user, agent_name="dead", doc_path="x.md",
        activity="read", description=None, ttl=timedelta(seconds=-60),
    )
    rows = agent_activity.list_for_doc("x.md")
    names = [r.agent_name for r in rows]
    assert names == ["live"]
    # list_all_expired sees the dead one (and not the live one).
    expired_names = [r.agent_name for r in agent_activity.list_all_expired()]
    assert "dead" in expired_names
    assert "live" not in expired_names


def test_get_and_delete_by_natural_key_round_trip(seeded_user):
    from app.wiki import agent_activity

    agent_activity.upsert_activity(
        user_id=seeded_user, agent_name=None, doc_path="x.md",
        activity="wrote", description="initial",
    )
    row = agent_activity.get_by_natural_key(
        user_id=seeded_user, agent_name=None, doc_path="x.md", activity="wrote",
    )
    assert row is not None
    assert row.description == "initial"

    agent_activity.delete_by_natural_key(
        user_id=seeded_user, agent_name=None, doc_path="x.md", activity="wrote",
    )
    assert agent_activity.get_by_natural_key(
        user_id=seeded_user, agent_name=None, doc_path="x.md", activity="wrote",
    ) is None


def test_delete_for_doc_removes_all_activities_on_path(seeded_user):
    """A doc deletion should clear every row attached to that path,
    regardless of agent name or activity kind.
    """
    from app.wiki import agent_activity

    agent_activity.upsert_activity(
        user_id=seeded_user, agent_name=None, doc_path="x.md",
        activity="read", description=None,
    )
    agent_activity.upsert_activity(
        user_id=seeded_user, agent_name="a", doc_path="x.md",
        activity="wrote", description="touched",
    )
    agent_activity.upsert_activity(
        user_id=seeded_user, agent_name=None, doc_path="other.md",
        activity="read", description=None,
    )

    agent_activity.delete_for_doc("x.md")
    assert agent_activity.list_for_doc("x.md") == []
    # Unrelated doc's row is untouched.
    assert len(agent_activity.list_for_doc("other.md")) == 1


def test_rename_doc_repoints_existing_rows(seeded_user):
    from app.wiki import agent_activity

    agent_activity.upsert_activity(
        user_id=seeded_user, agent_name=None, doc_path="old.md",
        activity="read", description=None,
    )
    agent_activity.rename_doc("old.md", "new.md")
    assert agent_activity.list_for_doc("old.md") == []
    assert len(agent_activity.list_for_doc("new.md")) == 1


def test_replace_frontmatter_renders_block_from_db(seeded_user):
    from app.wiki import agent_activity

    agent_activity.upsert_activity(
        user_id=seeded_user, agent_name="status-watcher", doc_path="guide.md",
        activity="read", description=None,
    )
    rendered = agent_activity.replace_frontmatter(
        "# Guide\n\nbody\n", "guide.md"
    )
    fm_text, rest = agent_activity.split_frontmatter(rendered)
    assert fm_text is not None
    assert "agents:" in fm_text
    assert "owner: Author" in fm_text  # name is "Author", not the email
    assert "agent: status-watcher" in fm_text
    assert "activity: read" in fm_text
    # Original body survives.
    assert "# Guide" in rest


def test_replace_frontmatter_strips_old_block_when_no_active_rows(seeded_user):
    """If the DB has no rows for this doc, ``agents:`` must vanish from
    the rendered body — even if the incoming body still carries a
    stale block.
    """
    from app.wiki import agent_activity

    stale = (
        "---\n"
        "agents:\n"
        "  - owner: ghost\n"
        "    agent: gone\n"
        "    activity: read\n"
        "    description: N/A\n"
        "    expires_at: '2099-01-01T00:00:00+00:00'\n"
        "---\n"
        "# Guide\n\nbody\n"
    )
    out = agent_activity.replace_frontmatter(stale, "guide.md")
    assert "agents:" not in out
    assert "# Guide" in out


def test_replace_frontmatter_preserves_unrelated_extras(seeded_user):
    """Non-`agents` frontmatter (e.g. user-authored ``title``) is kept,
    and the registry block lands above it. Round-tripping a doc with
    user metadata must not eat that metadata.
    """
    from app.wiki import agent_activity

    agent_activity.upsert_activity(
        user_id=seeded_user, agent_name=None, doc_path="guide.md",
        activity="read", description=None,
    )
    incoming = (
        "---\n"
        "title: My Guide\n"
        "tags:\n"
        "  - one\n"
        "  - two\n"
        "---\n"
        "body\n"
    )
    out = agent_activity.replace_frontmatter(incoming, "guide.md")
    fm, rest = agent_activity.split_frontmatter(out)
    assert fm is not None
    assert "agents:" in fm
    assert "title: My Guide" in fm
    assert "- one" in fm and "- two" in fm
    assert rest == "body\n"


def test_replace_frontmatter_renders_special_chars_safely(seeded_user):
    """Owner/agent/description values that look YAML-special must be
    quoted by ``_yaml_str`` so the rendered block re-parses cleanly.
    """
    from app.auth import users as users_repo
    from app.wiki import agent_activity
    import yaml

    tricky = users_repo.create(email="t@x.com", password="hunter2-x", name="user: tricky")
    agent_activity.upsert_activity(
        user_id=tricky, agent_name="weird: agent",
        doc_path="x.md", activity="read",
        description="line1\nline2",
    )
    rendered = agent_activity.replace_frontmatter("body\n", "x.md")
    fm, _ = agent_activity.split_frontmatter(rendered)
    assert fm is not None
    # Re-parse the rendered block — if quoting was wrong, this would
    # raise or produce a different shape.
    parsed = yaml.safe_load(fm)
    entry = parsed["agents"][0]
    assert entry["owner"] == "user: tricky"
    assert entry["agent"] == "weird: agent"
    assert entry["description"] == "line1\nline2"
