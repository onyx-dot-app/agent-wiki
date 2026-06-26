"""Agent template tools: `list_templates` and `write_doc(template_id=…)`.

Real wiki repo + real DB. `current_user` is patched so write_doc attributes
the seeded policy to a known user.
"""
from __future__ import annotations

from typing import Any

from app.auth import User
from app.llm.agents.tools.list_templates import handle as list_templates
from app.llm.agents.tools.write_doc import handle as write_doc
from app.wiki import templates as templates_repo
from app.wiki import update_policy
from app.wiki import utils as wiki_utils
from tests._seed import seed_user


def _as_user(monkeypatch, uid: str = "u_a") -> str:
    seed_user(uid=uid, email=f"{uid}@x.com")
    user = User(id=uid, email=f"{uid}@x.com", name=None, is_admin=False)
    monkeypatch.setattr("app.auth.current_user", lambda: user)
    monkeypatch.setattr("app.llm.agents.tools.write_doc.current_user", lambda: user)
    return uid


def _template(**policy: Any) -> dict[str, Any]:
    return templates_repo.create(
        name="Meeting notes",
        body="# Notes\n",
        description="A meeting record.",
        system_prompt=None,
        created_by_user_id=None,
        **policy,
    )


def test_list_templates_returns_body_and_policy(tmp_db, tmp_repo) -> None:
    _template(ingestion_auto_update_disabled=True, update_instruction="Only facts.")
    out = list_templates({})
    t = next(x for x in out["templates"] if x["name"] == "Meeting notes")
    assert t["id"]
    assert t["body"] == "# Notes\n"
    assert t["description"] == "A meeting record."
    assert t["auto_update_disabled"] is True
    assert t["update_instruction"] == "Only facts."


def test_write_doc_with_template_id_seeds_policy(tmp_db, tmp_repo, monkeypatch) -> None:
    _as_user(monkeypatch)
    t = _template(
        ingestion_auto_update_disabled=True, update_instruction="Only facts."
    )
    out = write_doc(
        {
            "path": "team/n.md",
            "body": "# N\n\nx\n",
            "commit_message": "create",
            "template_id": t["id"],
        }
    )
    assert out.get("created") is True
    eff = update_policy.resolve_for_path("team/n.md")
    assert eff.ingestion_auto_update_disabled is True
    assert eff.update_instruction == "Only facts."


def test_write_doc_unknown_template_id_errors(tmp_db, tmp_repo, monkeypatch) -> None:
    _as_user(monkeypatch)
    out = write_doc(
        {
            "path": "team/m.md",
            "body": "# M\n",
            "commit_message": "c",
            "template_id": "nope",
        }
    )
    assert out.get("error") == "template_not_found"
    assert not wiki_utils.file_exists("team/m.md")  # nothing created


def test_write_doc_without_template_defaults_to_blank(
    tmp_db, tmp_repo, monkeypatch
) -> None:
    # No template_id → fall back to the Blank template, so the page still gets a
    # deliberate policy (Blank = auto-update off).
    _as_user(monkeypatch)
    templates_repo.create(
        name="Blank",
        body="",
        description="Start from an empty page.",
        system_prompt=None,
        ingestion_auto_update_disabled=True,
        created_by_user_id=None,
    )
    out = write_doc(
        {"path": "team/freeform.md", "body": "# Freeform\n\nx\n", "commit_message": "c"}
    )
    assert out.get("created") is True
    assert update_policy.resolve_for_path("team/freeform.md").ingestion_auto_update_disabled is True


def test_write_doc_sets_update_instruction_at_create(tmp_db, tmp_repo, monkeypatch) -> None:
    # The agent scopes the page at creation via update_instruction (+ overrides
    # any template default).
    _as_user(monkeypatch)
    out = write_doc(
        {
            "path": "team/scoped.md",
            "body": "# Scoped\n\nx\n",
            "commit_message": "c",
            "update_instruction": "Only track decisions and owners.",
            "ingestion_auto_update_disabled": False,
        }
    )
    assert out.get("created") is True
    eff = update_policy.resolve_for_path("team/scoped.md")
    assert eff.update_instruction == "Only track decisions and owners."
    assert eff.ingestion_auto_update_disabled is False  # explicit override of Blank default


def test_create_instruction_overrides_template(tmp_db, tmp_repo, monkeypatch) -> None:
    _as_user(monkeypatch)
    t = _template(update_instruction="template default instruction")
    out = write_doc(
        {
            "path": "team/override.md",
            "body": "# O\n\nx\n",
            "commit_message": "c",
            "template_id": t["id"],
            "update_instruction": "agent override instruction",
        }
    )
    assert out.get("created") is True
    assert (
        update_policy.resolve_for_path("team/override.md").update_instruction
        == "agent override instruction"
    )


def test_write_doc_warns_when_template_vanishes_post_validation(
    tmp_db, tmp_repo, monkeypatch
) -> None:
    # Template passes up-front validation but is gone by the post-commit seed
    # (race). The create still succeeds, but the tool surfaces a warning rather
    # than silently returning success with the default policy.
    _as_user(monkeypatch)
    t = _template(ingestion_auto_update_disabled=True)
    monkeypatch.setattr(
        "app.wiki.templates.apply_policy_to_page", lambda *a, **k: False
    )
    out = write_doc(
        {
            "path": "team/raced.md",
            "body": "# R\n\nx\n",
            "commit_message": "c",
            "template_id": t["id"],
        }
    )
    assert out.get("created") is True
    assert "warning" in out and "template was deleted" in out["warning"]
