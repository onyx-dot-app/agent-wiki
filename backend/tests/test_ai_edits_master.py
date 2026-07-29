"""The master "AI Auto-Edits" switch (``ai_edits_disabled``).

Tri-state, cascaded like its sibling fields. Effectively disabled, it
overrides both sub-settings at resolution time — ingestion off,
auto-management forbidden — WITHOUT changing their stored values, so
re-enabling restores the children. Enforcement views get the override by
default; the API display view opts out so the UI can render the preserved
children.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from app.wiki import update_policy
from tests._auth import login_fastapi
from tests._seed import seed_user


def test_master_overrides_but_preserves_children(tmp_repo):
    update_policy.set_policy(
        "team/page.md",
        ingestion_auto_update_disabled=False,
        ai_management_allowed=True,
    )
    update_policy.set_policy("team/page.md", ai_edits_disabled=True)

    # Enforcement view: both children forced off.
    eff = update_policy.resolve_for_path("team/page.md")
    assert eff.ai_edits_disabled is True
    assert eff.ingestion_auto_update_disabled is True
    assert eff.ai_management_allowed is False

    # Display view: children keep their stored values.
    disp = update_policy.resolve_for_path("team/page.md", apply_master=False)
    assert disp.ai_edits_disabled is True
    assert disp.ingestion_auto_update_disabled is False
    assert disp.ai_management_allowed is True

    # Re-enable: children come back exactly as set.
    update_policy.set_policy("team/page.md", ai_edits_disabled=None)
    eff = update_policy.resolve_for_path("team/page.md")
    assert eff.ai_edits_disabled is False
    assert eff.ingestion_auto_update_disabled is False
    assert eff.ai_management_allowed is True


def test_master_cascades_folder_to_page(tmp_repo):
    update_policy.set_policy("area", ai_edits_disabled=True)
    eff = update_policy.resolve_for_path("area/deep/page.md")
    assert eff.ai_edits_disabled is True
    assert eff.ingestion_auto_update_disabled is True
    # Most-granular-wins: a page can re-enable under a disabled folder.
    update_policy.set_policy("area/deep/page.md", ai_edits_disabled=False)
    eff = update_policy.resolve_for_path("area/deep/page.md")
    assert eff.ai_edits_disabled is False


def test_master_forbids_auto_management_tristate(tmp_repo):
    """The runner's batch resolution treats a disabled master as an explicit
    False — detectors skip the scope entirely."""
    update_policy.set_policy("locked", ai_edits_disabled=True)
    update_policy.set_policy("locked/page.md", ai_management_allowed=True)
    out = update_policy.resolve_ai_management_for_paths(["locked/page.md"])
    assert out["locked/page.md"] is False


def test_master_alone_keeps_the_row_alive(tmp_repo):
    update_policy.set_policy("solo/page.md", ai_edits_disabled=True)
    row = update_policy.get("solo/page.md")
    assert row is not None and row["ai_edits_disabled"] is True
    # Clearing it removes the now-empty row.
    assert update_policy.set_policy("solo/page.md", ai_edits_disabled=None) is None
    assert update_policy.get("solo/page.md") is None


def test_api_patch_and_display_roundtrip(tmp_repo):
    client = TestClient(create_app())
    uid = seed_user(uid="u1", email="u@x.com")
    login_fastapi(client, uid)
    from app.wiki import git as wiki_git

    wiki_git.commit_file("team/page.md", "# P\n", "seed", author=None)

    res = client.patch(
        "/api/update-policy",
        json={
            "path": "team/page.md",
            "ai_management_allowed": True,
            "ai_edits_disabled": True,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["explicit"]["ai_edits_disabled"] is True
    # Display view: preserved child rides alongside the master state.
    assert body["effective"]["ai_edits_disabled"] is True
    assert body["effective"]["ai_management_allowed"] is True

    # Clearing via explicit null.
    res = client.patch(
        "/api/update-policy",
        json={"path": "team/page.md", "ai_edits_disabled": None},
    )
    assert res.status_code == 200
    assert res.json()["effective"]["ai_edits_disabled"] is False
