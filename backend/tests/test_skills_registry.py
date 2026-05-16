"""Tests for the skills registry: structure, tool resolution, history derivation."""
from __future__ import annotations

import pytest

from app.llm.agents import skills as skill_registry
from app.llm.agents import tools as tool_registry


def test_expected_skills_present():
    assert set(skill_registry.SKILLS.keys()) == {
        "triggers",
        "modify_wiki",
        "web_search",
        "ux_explanation",
        "bash",
    }


def test_every_referenced_tool_resolves():
    """No typos in skill `tool_names` — each must point to a real tool spec."""
    for skill in skill_registry.SKILLS.values():
        for tool_name in skill.tool_names:
            spec = tool_registry.spec_by_name(tool_name)
            assert spec["name"] == tool_name


def test_base_tools_resolve():
    specs = skill_registry.base_tool_specs()
    names = [s["name"] for s in specs]
    assert names == ["search_wiki", "read_page"]


def test_load_skill_spec_lists_skills_in_enum():
    enum = skill_registry.build_load_skill_spec()["input_schema"]["properties"]["name"]["enum"]
    # Skills with no available tools (e.g. web_search without keys) are
    # hidden, so the enum is a subset of all registered skills.
    assert set(enum).issubset(set(skill_registry.SKILLS.keys()))
    assert set(enum), "at least one skill should always be available"


def test_load_skill_handler_returns_instructions():
    out = skill_registry.load_skill_handler({"name": "triggers"})
    assert isinstance(out, str)
    assert "create_trigger" in out


def test_load_skill_handler_rejects_unknown():
    with pytest.raises(ValueError, match="unknown skill"):
        skill_registry.load_skill_handler({"name": "nope"})


def test_active_skills_empty_history():
    assert skill_registry.active_skills([]) == set()


def test_active_skills_single_load():
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "1", "name": "load_skill", "arguments": {"name": "triggers"}}
            ],
        }
    ]
    assert skill_registry.active_skills(messages) == {"triggers"}


def test_active_skills_repeated_loads_deduped():
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "1", "name": "load_skill", "arguments": {"name": "triggers"}},
                {"id": "2", "name": "load_skill", "arguments": {"name": "triggers"}},
            ],
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "3", "name": "load_skill", "arguments": {"name": "bash"}}
            ],
        },
    ]
    assert skill_registry.active_skills(messages) == {"triggers", "bash"}


def test_active_skills_unknown_skill_ignored():
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "1", "name": "load_skill", "arguments": {"name": "nope"}}
            ],
        }
    ]
    assert skill_registry.active_skills(messages) == set()


def test_specs_for_active_skills_returns_tools():
    specs = skill_registry.specs_for_active_skills({"triggers"})
    names = {s["name"] for s in specs}
    assert names == {"create_trigger", "update_trigger", "get_trigger_destinations"}


def test_specs_for_active_skills_unknown_skipped():
    assert skill_registry.specs_for_active_skills({"nope"}) == []
