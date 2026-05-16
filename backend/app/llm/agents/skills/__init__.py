"""Skills registry for the chat agent.

Skills group workflow-shaped tools behind a `load_skill` meta-tool. Only the
base tools (`search_wiki`, `read_page`) plus `load_skill` are exposed to the
model up front. When the model calls `load_skill(name)`, the handler returns
that skill's instruction markdown and the per-turn tool list assembly in the
chat loop picks up the newly active skill on the next iteration.

Active skill state is *derived from message history* — see
`active_skills(messages)`. There is no mutable registry state, so resuming a
persisted conversation re-activates the right skills automatically.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict

from app.llm.agents import tools as tool_registry

_DIR = Path(__file__).parent

BASE_TOOL_NAMES: tuple[str, ...] = ("search_wiki", "read_page")

LOAD_SKILL_TOOL_NAME = "load_skill"


class Skill(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    tool_names: tuple[str, ...]
    instructions: str


def _load_md(name: str) -> str:
    return (_DIR / f"{name}.md").read_text()


SKILLS: dict[str, Skill] = {
    "triggers": Skill(
        name="triggers",
        description="create/update/list NL triggers on wiki pages and folders",
        tool_names=("create_trigger", "update_trigger", "get_trigger_destinations"),
        instructions=_load_md("triggers"),
    ),
    "modify_wiki": Skill(
        name="modify_wiki",
        description="read/edit/create/move wiki pages and directories",
        tool_names=(
            "read_doc",
            "write_doc",
            "edit_doc",
            "multi_edit",
            "apply_patch",
            "move_path",
            "create_directory",
            "update_doc_nl",
            "list_history",
        ),
        instructions=_load_md("modify_wiki"),
    ),
    "web_search": Skill(
        name="web_search",
        description="search the public web and fetch page contents",
        tool_names=("web_search", "open_urls"),
        instructions=_load_md("web_search"),
    ),
    "ux_explanation": Skill(
        name="ux_explanation",
        description="explain how Agent Wiki works or answer wiki Q&A via a sub-agent",
        tool_names=("explain_functionality", "ask_nl_question"),
        instructions=_load_md("ux_explanation"),
    ),
    "bash": Skill(
        name="bash",
        description="run read-only shell commands (cat/find/grep/ls/head/tail/wc) against the wiki tree",
        tool_names=("run_bash",),
        instructions=_load_md("bash"),
    ),
}


def _available_skill_names() -> list[str]:
    """Skill names that currently have at least one runnable tool.

    A skill whose tools are *all* unavailable (e.g. ``web_search`` with no
    Serper/Firecrawl key) is hidden from ``load_skill`` so the model can't
    pick something that would unlock no tools.
    """
    out: list[str] = []
    for skill in SKILLS.values():
        if any(tool_registry.is_available(t) for t in skill.tool_names):
            out.append(skill.name)
    return out


def build_load_skill_spec() -> dict[str, Any]:
    """Build the ``load_skill`` meta-tool spec from currently-available skills.

    Called per turn instead of cached at import — availability can change
    when the admin updates provider keys at runtime, and we want the next
    request to reflect that without a process restart.
    """
    visible = _available_skill_names()
    bullets = "\n".join(f"- {SKILLS[n].name}: {SKILLS[n].description}" for n in visible)
    return {
        "name": LOAD_SKILL_TOOL_NAME,
        "description": (
            "Load a skill to gain access to additional tools. Call this once per "
            "skill needed; tools remain available for the rest of the conversation. "
            "Available skills:\n" + bullets
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "enum": visible,
                }
            },
            "required": ["name"],
        },
    }


def _validate_registry() -> None:
    """Check at import time that every referenced tool exists.

    Raising here gives a loud failure on a typo'd tool name instead of a
    confusing runtime ``unknown tool`` later. Availability is not checked
    here — tools can legitimately be present-but-unconfigured.
    """
    for skill in SKILLS.values():
        for tool_name in skill.tool_names:
            tool_registry.spec_by_name(tool_name)
    for tool_name in BASE_TOOL_NAMES:
        tool_registry.spec_by_name(tool_name)


_validate_registry()


def base_tool_specs() -> list[dict[str, Any]]:
    """Tool specs for the always-available base toolset (no `load_skill`).

    Filters out any base tool whose ``available()`` returns False.
    """
    return [
        tool_registry.spec_by_name(n)
        for n in BASE_TOOL_NAMES
        if tool_registry.is_available(n)
    ]


def specs_for_active_skills(active: set[str]) -> list[dict[str, Any]]:
    """Tool specs unlocked by the given set of active skill names.

    Tools whose prerequisites aren't satisfied right now are dropped so
    the LLM doesn't see options it can't use.
    """
    out: list[dict[str, Any]] = []
    for skill_name in active:
        skill = SKILLS.get(skill_name)
        if skill is None:
            continue
        for tool_name in skill.tool_names:
            if tool_registry.is_available(tool_name):
                out.append(tool_registry.spec_by_name(tool_name))
    return out


def active_skills(messages: list[dict[str, Any]]) -> set[str]:
    """Derive the set of currently-active skills from prior `load_skill` calls.

    Sticky-for-conversation: a skill loaded in turn N stays active for every
    turn after. State is reconstructed from the message history each turn,
    so a resumed conversation picks up where it left off.
    """
    active: set[str] = set()
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        raw_calls = msg.get("tool_calls")
        if not isinstance(raw_calls, list):
            continue
        for call in cast(list[Any], raw_calls):
            if not isinstance(call, dict):
                continue
            call_dict = cast(dict[str, Any], call)
            if call_dict.get("name") != LOAD_SKILL_TOOL_NAME:
                continue
            raw_args = call_dict.get("arguments")
            if not isinstance(raw_args, dict):
                continue
            skill_name = cast(dict[str, Any], raw_args).get("name")
            if isinstance(skill_name, str) and skill_name in SKILLS:
                active.add(skill_name)
    return active


def load_skill_handler(args: dict[str, Any]) -> str:
    """Dispatch handler for the `load_skill` tool. Returns the skill's instructions.

    No mutable side effect — the caller's tool-list assembly will pick up the
    new active skill via `active_skills(messages)` on the next turn.
    """
    name = args.get("name")
    if not isinstance(name, str):
        raise ValueError("load_skill: missing 'name' argument")
    skill = SKILLS.get(name)
    if skill is None:
        raise ValueError(f"unknown skill: {name}")
    return skill.instructions
