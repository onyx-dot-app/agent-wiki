"""Tests that the chat loop's per-turn tool list reflects loaded skills.

Patches `client.stream` to script the model side. Captures the `tools` arg
sent to the LLM each turn so we can assert what was visible to the model.
"""
from __future__ import annotations

import pytest

from app.llm import client as llm_client
from app.llm.agents import chat as chat_agent


@pytest.fixture
def script_stream(monkeypatch):
    """Replace `client.stream` with a scripted sequence and capture `tools`."""
    scripted: list[list[dict]] = []
    captured_tools: list[list[dict] | None] = []

    def install(events: list[list[dict]]):
        scripted.extend(events)

    def fake_stream(messages, *, model=None, provider=None, tools=None, max_tokens=4096):
        if not scripted:
            raise AssertionError("script_stream exhausted")
        captured_tools.append(list(tools) if tools is not None else None)
        for ev in scripted.pop(0):
            yield ev

    monkeypatch.setattr(llm_client, "stream", fake_stream)
    monkeypatch.setattr(chat_agent.client, "stream", fake_stream)
    return install, captured_tools


def _done():
    return {"type": "done", "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}


def _tool_names(tools_list: list[dict] | None) -> set[str]:
    return {t["name"] for t in tools_list or []}


def test_first_turn_has_only_base_and_load_skill(script_stream):
    install, captured = script_stream
    install([[{"type": "text_delta", "text": "hi"}, _done()]])

    messages: list[dict] = [{"role": "user", "content": "hi"}]
    chat_agent.run_chat_loop(
        messages,
        tools_provider=chat_agent._chat_tools_for_turn,
        tool_dispatch=chat_agent._chat_dispatch,
    )

    assert _tool_names(captured[0]) == {"search_wiki", "read_page", "load_skill"}


def test_load_skill_unlocks_skill_tools_on_next_turn(script_stream):
    install, captured = script_stream
    install(
        [
            # Iteration 1: model loads the triggers skill
            [
                {
                    "type": "tool_call",
                    "id": "c1",
                    "name": "load_skill",
                    "arguments": {"name": "triggers"},
                },
                _done(),
            ],
            # Iteration 2: final assistant message
            [{"type": "text_delta", "text": "ok"}, _done()],
        ]
    )

    messages: list[dict] = [{"role": "user", "content": "set up a trigger"}]
    chat_agent.run_chat_loop(
        messages,
        tools_provider=chat_agent._chat_tools_for_turn,
        tool_dispatch=chat_agent._chat_dispatch,
    )

    # Turn 1 — only base + load_skill visible
    assert "create_trigger" not in _tool_names(captured[0])
    # Turn 2 — triggers skill tools now visible
    turn2 = _tool_names(captured[1])
    assert {"create_trigger", "update_trigger", "get_trigger_destinations"} <= turn2
    # Base tools still visible
    assert {"search_wiki", "read_page", "load_skill"} <= turn2


def test_skill_remains_active_across_subsequent_turns(script_stream):
    install, captured = script_stream
    install(
        [
            # Turn 1: load triggers skill
            [
                {
                    "type": "tool_call",
                    "id": "c1",
                    "name": "load_skill",
                    "arguments": {"name": "triggers"},
                },
                _done(),
            ],
            # Turn 2: model thinks (no tool calls would terminate) — emit a
            # search to keep loop going
            [
                {
                    "type": "tool_call",
                    "id": "c2",
                    "name": "search_wiki",
                    "arguments": {"query": "x"},
                },
                _done(),
            ],
            # Turn 3: final
            [{"type": "text_delta", "text": "done"}, _done()],
        ]
    )

    def fake_dispatch(name, args):
        if name == "load_skill":
            return chat_agent.skill_registry.load_skill_handler(args)
        if name == "search_wiki":
            return []
        raise AssertionError(f"unexpected tool {name}")

    messages: list[dict] = [{"role": "user", "content": "trigger work"}]
    chat_agent.run_chat_loop(
        messages,
        tools_provider=chat_agent._chat_tools_for_turn,
        tool_dispatch=fake_dispatch,
    )

    # Triggers tools must be present on turn 2 AND turn 3 (sticky).
    for turn in (1, 2):  # captured indices 1 and 2 are turns 2 and 3
        names = _tool_names(captured[turn])
        assert "create_trigger" in names, f"turn {turn + 1} missing create_trigger"
