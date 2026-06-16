"""Tests for app.wiki.update_policy — the per-page/per-folder update policy.

Covers the most-granular-wins cascade and the patch/delete semantics of
set_policy.
"""
from __future__ import annotations

from typing import Any

from app.wiki import update_policy


# --------------------------------------------------------------------------- #
# Resolver cascade                                                             #
# --------------------------------------------------------------------------- #


def test_resolve_defaults_when_no_rows(tmp_db):
    resolved = update_policy.resolve_for_path("a/b/page.md")
    assert resolved.ingestion_auto_update_disabled is False
    assert resolved.update_instruction is None


def test_doc_level_disable(tmp_db):
    update_policy.set_policy("a/page.md", ingestion_auto_update_disabled=True)
    assert update_policy.is_ingest_disabled("a/page.md") is True


def test_folder_disable_cascades_to_doc(tmp_db):
    update_policy.set_policy("a", ingestion_auto_update_disabled=True)
    assert update_policy.is_ingest_disabled("a/b/page.md") is True


def test_granular_page_re_enables_under_disabled_folder(tmp_db):
    update_policy.set_policy("a", ingestion_auto_update_disabled=True)
    update_policy.set_policy("a/page.md", ingestion_auto_update_disabled=False)
    # Most-granular wins: the page opts back in even though the folder is off.
    assert update_policy.is_ingest_disabled("a/page.md") is False
    # A sibling with no own row still inherits the folder's disable.
    assert update_policy.is_ingest_disabled("a/other.md") is True


def test_root_scope_cascades_everywhere(tmp_db):
    update_policy.set_policy("", ingestion_auto_update_disabled=True)
    assert update_policy.is_ingest_disabled("anything/deep/page.md") is True


def test_instruction_closest_scope_wins(tmp_db):
    update_policy.set_policy("a", update_instruction="folder rule")
    update_policy.set_policy("a/page.md", update_instruction="page rule")
    assert update_policy.resolve_for_path("a/page.md").update_instruction == "page rule"
    # A sibling without its own instruction falls back to the folder's.
    assert update_policy.resolve_for_path("a/other.md").update_instruction == "folder rule"


def test_disabled_paths_batch(tmp_db):
    update_policy.set_policy("a", ingestion_auto_update_disabled=True)  # folder off
    update_policy.set_policy("a/on.md", ingestion_auto_update_disabled=False)  # re-enabled
    update_policy.set_policy("b.md", ingestion_auto_update_disabled=True)
    result = update_policy.disabled_paths(["a/off.md", "a/on.md", "b.md", "c.md"])
    # a/off.md inherits the folder disable; a/on.md overrides; b.md is explicit;
    # c.md has no policy.
    assert result == {"a/off.md", "b.md"}


def test_disabled_paths_empty(tmp_db):
    assert update_policy.disabled_paths([]) == set()


def test_resolve_for_paths_batch(tmp_db):
    update_policy.set_policy("a", update_instruction="folder rule")
    update_policy.set_policy("a/p.md", ingestion_auto_update_disabled=True)
    res = update_policy.resolve_for_paths(["a/p.md", "a/q.md", "c.md"])
    # a/p.md: own disable + inherited instruction
    assert res["a/p.md"].ingestion_auto_update_disabled is True
    assert res["a/p.md"].update_instruction == "folder rule"
    # a/q.md: inherits the folder instruction, enabled by default
    assert res["a/q.md"].ingestion_auto_update_disabled is False
    assert res["a/q.md"].update_instruction == "folder rule"
    # c.md: nothing applies
    assert res["c.md"].ingestion_auto_update_disabled is False
    assert res["c.md"].update_instruction is None


def test_nl_updater_injects_inherited_instruction(tmp_db, monkeypatch):
    from app.llm.agents import nl_updater
    from app.llm.client import CompletionResult

    update_policy.set_policy("team", update_instruction="Keep it terse.")  # folder
    captured: dict[str, Any] = {}

    def fake_complete(*, messages, **kwargs):
        captured["messages"] = messages
        return CompletionResult(text="NO_CHANGE", tool_calls=[], stop_reason="end_turn")

    monkeypatch.setattr(nl_updater.client, "complete", fake_complete)
    nl_updater.process_instruction(
        wiki_path="team/page.md", current_body="# P", payload={"instruction": "x"},
        source="test",
    )
    user_msg = captured["messages"][1]["content"]
    assert "Update instruction for this page" in user_msg
    assert "Keep it terse." in user_msg  # inherited from the folder


def test_nl_updater_omits_section_when_no_policy(tmp_db, monkeypatch):
    from app.llm.agents import nl_updater
    from app.llm.client import CompletionResult

    captured: dict[str, Any] = {}

    def fake_complete(*, messages, **kwargs):
        captured["messages"] = messages
        return CompletionResult(text="NO_CHANGE", tool_calls=[], stop_reason="end_turn")

    monkeypatch.setattr(nl_updater.client, "complete", fake_complete)
    nl_updater.process_instruction(
        wiki_path="a/page.md", current_body="# A", payload={"instruction": "x"},
        source="test",
    )
    assert "Update instruction for this page" not in captured["messages"][1]["content"]


def test_nl_updater_proceeds_when_policy_store_unavailable(monkeypatch):
    """No DB (offline eval): the advisory policy lookup must not fail the update."""
    from app.llm.agents import nl_updater
    from app.llm.client import CompletionResult

    def boom(_path):
        raise RuntimeError("no database")

    monkeypatch.setattr(nl_updater.update_policy, "resolve_for_path", boom)
    captured: dict[str, Any] = {}

    def fake_complete(*, messages, **kwargs):
        captured["messages"] = messages
        return CompletionResult(text="NO_CHANGE", tool_calls=[], stop_reason="end_turn")

    monkeypatch.setattr(nl_updater.client, "complete", fake_complete)
    result = nl_updater.process_instruction(
        wiki_path="a/page.md", current_body="# A", payload={"instruction": "x"},
        source="test",
    )
    assert result is None
    assert "Update instruction for this page" not in captured["messages"][1]["content"]


def test_fields_resolve_independently(tmp_db):
    update_policy.set_policy("a", ingestion_auto_update_disabled=True)
    update_policy.set_policy("a/page.md", update_instruction="terse")
    resolved = update_policy.resolve_for_path("a/page.md")
    # disable inherited from the folder; instruction from the page.
    assert resolved.ingestion_auto_update_disabled is True
    assert resolved.update_instruction == "terse"


# --------------------------------------------------------------------------- #
# set_policy patch + delete semantics                                         #
# --------------------------------------------------------------------------- #


def test_set_policy_patches_only_given_fields(tmp_db):
    update_policy.set_policy("a/page.md", ingestion_auto_update_disabled=True)
    # Second call sets only the instruction; the disable flag is left intact.
    update_policy.set_policy("a/page.md", update_instruction="keep")
    row = update_policy.get("a/page.md")
    assert row is not None
    assert row["ingestion_auto_update_disabled"] is True
    assert row["update_instruction"] == "keep"


def test_clearing_both_fields_deletes_row(tmp_db):
    update_policy.set_policy("a/page.md", ingestion_auto_update_disabled=True)
    result = update_policy.set_policy(
        "a/page.md", ingestion_auto_update_disabled=None, update_instruction=None
    )
    assert result is None
    assert update_policy.get("a/page.md") is None


def test_empty_instruction_is_cleared(tmp_db):
    result = update_policy.set_policy("a/page.md", update_instruction="")
    # Only field set is an empty instruction → nothing to store.
    assert result is None
    assert update_policy.get("a/page.md") is None


def test_kind_inferred_from_path(tmp_db):
    update_policy.set_policy("a/page.md", ingestion_auto_update_disabled=True)
    update_policy.set_policy("a", ingestion_auto_update_disabled=True)
    update_policy.set_policy("", ingestion_auto_update_disabled=True)
    page = update_policy.get("a/page.md")
    folder = update_policy.get("a")
    root = update_policy.get("")
    assert page is not None and page["kind"] == "page"
    assert folder is not None and folder["kind"] == "folder"
    assert root is not None and root["kind"] == "folder"


# --------------------------------------------------------------------------- #
# Lifecycle — policy follows page/folder move + drops on delete               #
# --------------------------------------------------------------------------- #


def test_on_path_moved_rekeys_page(tmp_db):
    update_policy.set_policy(
        "a.md", ingestion_auto_update_disabled=True, update_instruction="x"
    )
    update_policy.on_path_moved([("a.md", "b.md")])
    assert update_policy.get("a.md") is None
    moved = update_policy.get("b.md")
    assert moved is not None
    assert moved["ingestion_auto_update_disabled"] is True
    assert moved["update_instruction"] == "x"
    assert moved["kind"] == "page"


def test_on_path_moved_folder_rename_carries_subtree(tmp_db):
    update_policy.set_policy("proj", update_instruction="folder rule")
    update_policy.set_policy("proj/sub", ingestion_auto_update_disabled=True)
    update_policy.set_policy("proj/x.md", ingestion_auto_update_disabled=True)
    # A directory rename surfaces as one move pair per nested file.
    update_policy.on_path_moved([("proj/x.md", "work/x.md")])
    assert update_policy.get("proj") is None
    assert update_policy.get("proj/sub") is None
    assert update_policy.get("proj/x.md") is None
    work = update_policy.get("work")
    assert work is not None and work["update_instruction"] == "folder rule"
    assert update_policy.get("work/sub") is not None
    assert update_policy.get("work/x.md") is not None


def test_on_page_deleted_drops_row(tmp_db):
    update_policy.set_policy("a.md", ingestion_auto_update_disabled=True)
    update_policy.on_page_deleted("a.md")
    assert update_policy.get("a.md") is None
