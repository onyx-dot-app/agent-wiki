"""Flow 5 — agent reads register in the registry and surface as the
``agents`` field on the read tool / via ``GET /file/activity``.

The agent-activity registry is DB-only: a ``read_page`` call upserts a
``read`` row but does NOT touch the doc body. The body the model sees
is the raw markdown the page was written with. Co-occupancy
information rides on a separate channel — the ``agents`` list on the
tool response, and the ``/api/wiki/file/activity`` endpoint that
backs the wiki UI panel.

This test drives the real ``read_page`` handler inside a Flask request
context so ``current_user()`` resolves; the rest of the path (DB
upsert, response assembly) runs for real.

Caveat: under ``immediate_queues`` every scheduled task runs
synchronously, ``eta`` and all. ``mark_doc_read`` schedules a 24h-out
cleanup that would otherwise delete the row before we observe it, so
the test stubs ``schedule_cleanup_for_natural_key`` to a no-op. In
production the eta keeps the cleanup pending.
"""
from __future__ import annotations

from app.auth import load_user, set_current_user


def test_agent_read_surfaces_via_tool_response_and_api(integration, monkeypatch):
    monkeypatch.setattr(
        "app.tasks.agent_activity.schedule_cleanup_for_natural_key",
        lambda **kw: None,
    )

    uid = integration.signup_and_signin(email="agent-user@x.com")

    raw_body = "# Guide\n\noriginal body\n"
    integration.put_doc("guide.md", raw_body)

    from app.llm.agents.tools import read_page
    from app.wiki import agent_activity, git as wiki_git

    # The on-disk body is exactly what was PUT — no managed block.
    on_disk_before = wiki_git.read_file("guide.md")
    assert on_disk_before == raw_body

    token = agent_activity.agent_name_var.set("status-watcher")
    try:
        with set_current_user(load_user(uid)):

            result = read_page.handle({"path": "guide.md"})
    finally:
        agent_activity.agent_name_var.reset(token)

    assert "error" not in result, result
    assert result["body"] == raw_body, "read_page must return the raw body unchanged"

    # The tool response carries the freshly registered activity row.
    agents = result["agents"]
    assert len(agents) == 1, agents
    entry = agents[0]
    assert entry["owner_display"] == "U"  # signup default
    assert entry["agent_name"] == "status-watcher"
    assert entry["activity"] == "read"

    # The on-disk body did NOT change — read is no longer a write.
    on_disk_after = wiki_git.read_file("guide.md")
    assert on_disk_after == on_disk_before

    # GET /api/wiki/file/activity reflects the same row.
    resp = integration.client.get("/api/wiki/file/activity?path=guide.md")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["path"] == "guide.md"
    assert len(payload["agents"]) == 1
    api_entry = payload["agents"][0]
    assert api_entry["owner_display"] == "U"
    assert api_entry["agent_name"] == "status-watcher"
    assert api_entry["activity"] == "read"


def test_agent_read_does_not_mint_commits(integration, monkeypatch):
    """Regression: the whole point of moving activity to the DB. A
    HEAD read must not advance the doc's HEAD — otherwise any
    ``base_sha`` an agent is holding goes stale on every read.
    """
    monkeypatch.setattr(
        "app.tasks.agent_activity.schedule_cleanup_for_natural_key",
        lambda **kw: None,
    )

    uid = integration.signup_and_signin(email="agent-user@x.com")
    integration.put_doc("guide.md", "# Guide\n\noriginal body\n")

    from app.llm.agents.tools import read_page
    from app.wiki import git as wiki_git

    sha_before = wiki_git.head_sha_for_path("guide.md")

    with set_current_user(load_user(uid)):
        # Multiple reads in quick succession — pre-migration, each one
        # would have upserted, slid expires_at, re-rendered the
        # frontmatter, and committed.
        for _ in range(3):
            read_page.handle({"path": "guide.md"})

    sha_after = wiki_git.head_sha_for_path("guide.md")
    assert sha_after == sha_before, (
        "reads must not advance HEAD; otherwise base_sha goes stale"
    )


def test_agent_write_registers_wrote_activity(integration, monkeypatch):
    """``commit_and_fan_out`` upserts a ``wrote`` row alongside the
    commit so the registry reflects authorship. Complement to the
    read-side coverage in ``test_agent_read_surfaces_via_tool_response_and_api``.

    The natural key is ``(user, agent)`` — the write overwrites the
    prior read row in place, so only one row remains.
    """
    monkeypatch.setattr(
        "app.tasks.agent_activity.schedule_cleanup_for_natural_key",
        lambda **kw: None,
    )

    uid = integration.signup_and_signin(email="writer@x.com")
    integration.put_doc("guide.md", "# Guide\n\noriginal body\n")

    from app.llm.agents.tools import read_page, write_doc
    from app.wiki import agent_activity, git as wiki_git

    with set_current_user(load_user(uid)):

        read_page.handle({"path": "guide.md"})
        sha = wiki_git.head_sha_for_path("guide.md")
        result = write_doc.handle({
            "path": "guide.md",
            "body": "# Guide\n\nrewritten\n",
            "commit_message": "tweak heading",
            "base_sha": sha,
        })
        assert "error" not in result, result

    rows = agent_activity.list_for_doc("guide.md")
    assert len(rows) == 1
    assert rows[0].activity == "wrote"
    assert rows[0].description == "tweak heading"


def test_write_doc_expires_in_seconds_overrides_ttl(integration, monkeypatch):
    """``expires_in_seconds`` on a write tool sets the row's TTL to that
    value rather than the 24h default.
    """
    monkeypatch.setattr(
        "app.tasks.agent_activity.schedule_cleanup_for_natural_key",
        lambda **kw: None,
    )

    uid = integration.signup_and_signin(email="time-traveler@x.com")
    integration.put_doc("guide.md", "# Guide\n\noriginal\n")

    from datetime import datetime, timezone
    from app.llm.agents.tools import write_doc
    from app.wiki import agent_activity, git as wiki_git

    with set_current_user(load_user(uid)):

        sha = wiki_git.head_sha_for_path("guide.md")
        result = write_doc.handle({
            "path": "guide.md",
            "body": "# Guide\n\nrewritten\n",
            "commit_message": "tighten",
            "base_sha": sha,
            "expires_in_seconds": 300,
        })
        assert "error" not in result, result

    rows = agent_activity.list_for_doc("guide.md")
    assert len(rows) == 1
    expires_at = datetime.fromisoformat(rows[0].expires_at)
    delta = (expires_at - datetime.now(timezone.utc)).total_seconds()
    # ~5 minutes from now, well under the 24h default. Allow a few seconds
    # of slack for test execution time.
    assert 250 < delta < 320


def test_write_doc_rejects_out_of_range_expires(integration, monkeypatch):
    monkeypatch.setattr(
        "app.tasks.agent_activity.schedule_cleanup_for_natural_key",
        lambda **kw: None,
    )

    uid = integration.signup_and_signin(email="bad-input@x.com")
    integration.put_doc("guide.md", "# Guide\n")

    from app.llm.agents.tools import write_doc
    from app.wiki import git as wiki_git

    with set_current_user(load_user(uid)):

        sha = wiki_git.head_sha_for_path("guide.md")
        too_short = write_doc.handle({
            "path": "guide.md", "body": "# x\n",
            "commit_message": "x", "base_sha": sha,
            "expires_in_seconds": 5,
        })
        assert "expires_in_seconds" in too_short.get("error", "")

        too_long = write_doc.handle({
            "path": "guide.md", "body": "# x\n",
            "commit_message": "x", "base_sha": sha,
            "expires_in_seconds": 99_999_999,
        })
        assert "expires_in_seconds" in too_long.get("error", "")


def test_read_doc_agents_field_only_on_head_reads(integration, monkeypatch):
    """``read_doc`` returns the live ``agents`` list on HEAD reads
    and an empty list on historical reads (we don't preserve activity
    history alongside content)."""
    monkeypatch.setattr(
        "app.tasks.agent_activity.schedule_cleanup_for_natural_key",
        lambda **kw: None,
    )

    uid = integration.signup_and_signin(email="historian@x.com")
    integration.put_doc("guide.md", "# Guide\n\nv1\n")
    from app.wiki import git as wiki_git
    v1_sha = wiki_git.head_sha_for_path("guide.md")
    integration.put_doc("guide.md", "# Guide\n\nv2\n")

    from app.llm.agents.tools import read_doc

    with set_current_user(load_user(uid)):

        head_result = read_doc.handle({"path": "guide.md"})
        assert head_result["is_head"] is True
        assert len(head_result["agents"]) == 1, head_result["agents"]
        assert head_result["agents"][0]["activity"] == "read"

        historical = read_doc.handle({"path": "guide.md", "sha": v1_sha})
        assert historical["is_head"] is False
        assert historical["body"] == "# Guide\n\nv1\n"
        assert historical["agents"] == [], (
            "historical reads must not surface current activity rows"
        )


def test_doc_body_starting_with_yaml_fence_round_trips(integration, monkeypatch):
    """A doc whose body legitimately begins with ``---\\n…`` (e.g. the
    user is documenting YAML or a frontmatter format) must survive
    read+write unchanged. Pre-migration the frontmatter parser would
    have eaten or rewritten this; now there's no parser at all on the
    commit path."""
    monkeypatch.setattr(
        "app.tasks.agent_activity.schedule_cleanup_for_natural_key",
        lambda **kw: None,
    )

    uid = integration.signup_and_signin(email="yaml-fan@x.com")
    yaml_body = (
        "---\n"
        "title: Example Frontmatter Doc\n"
        "tags: [foo, bar]\n"
        "---\n"
        "\n"
        "# YAML Example\n"
        "\n"
        "Body that documents what frontmatter can look like.\n"
    )
    integration.put_doc("yaml-doc.md", yaml_body)

    from app.llm.agents.tools import read_page
    from app.wiki import git as wiki_git

    assert wiki_git.read_file("yaml-doc.md") == yaml_body

    with set_current_user(load_user(uid)):
        result = read_page.handle({"path": "yaml-doc.md"})

    assert result["body"] == yaml_body
    assert wiki_git.read_file("yaml-doc.md") == yaml_body


def test_agent_read_anonymous_renders_na(integration, monkeypatch):
    """No ``agent_name_var`` set → entry comes back with agent_name=None."""
    monkeypatch.setattr(
        "app.tasks.agent_activity.schedule_cleanup_for_natural_key",
        lambda **kw: None,
    )

    uid = integration.signup_and_signin(email="solo@x.com")
    integration.put_doc("notes.md", "# Notes\n\nbody\n")

    from app.llm.agents.tools import read_page

    with set_current_user(load_user(uid)):
        result = read_page.handle({"path": "notes.md"})

    assert "error" not in result, result
    agents = result["agents"]
    assert len(agents) == 1
    assert agents[0]["agent_name"] is None
    assert agents[0]["owner_display"] == "U"
