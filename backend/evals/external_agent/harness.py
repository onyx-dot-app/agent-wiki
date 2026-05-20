"""Drive a real LLM agent through an MCP-shaped tool surface against an
in-memory wiki, then score which docs it chose to update.

This is the WHEN-axis eval for an external agent. The model is wrapped in
``run_chat_loop`` (the same primitive the production chat agent uses) and
given three tools that mirror the MCP surface external agents see:

* ``list_docs()`` — paths + 1-line summaries of every page in the wiki
* ``read_doc(path)`` — full body of a page
* ``update_doc_nl(path, instruction)`` — apply an NL instruction. Drives
  the same ``wiki_updater.process_instruction`` agent as the real MCP
  path. The harness applies the returned body to the in-memory wiki.

A scenario specifies (a) the seed wiki state, (b) a task prompt for the
agent, (c) hidden ground truth of which paths SHOULD be updated and what
facts must appear/persist. Reusing the Phase 1 quality scorers means the
HOW axis numbers are directly comparable between internal and external.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.llm.agents.wiki_updater import process_instruction
from app.llm.agents.chat import run_chat_loop

from evals.schema import FactClaim


log = logging.getLogger(__name__)

_EXTERNAL_AGENT_SYSTEM = (
    "You are an external agent connected to an org wiki via MCP. You have three tools:\n"
    "  - list_docs()                          : list every doc path with a 1-line summary\n"
    "  - read_doc(path)                       : return the full body of a doc\n"
    "  - update_doc_nl(path, instruction)     : apply a natural-language instruction to a doc\n"
    "\n"
    "Behavior:\n"
    "  - Only call update_doc_nl on docs whose content actually needs to change.\n"
    "  - Don't update unrelated pages.\n"
    "  - If a page already reflects the relevant information, do not update it.\n"
    "  - When you are done, send a short summary in plain text (no tools).\n"
)


class ExpectedUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    facts_present: list[FactClaim] = Field(default_factory=list)
    facts_preserved: list[FactClaim] = Field(default_factory=list)
    max_bloat_ratio: float = 2.0


class ScenarioDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    summary: str = ""
    body: str


class Scenario(BaseModel):
    """One end-to-end external-agent test case."""

    model_config = ConfigDict(frozen=True)

    id: str
    prompt: str
    wiki_state: list[ScenarioDocument]
    expected_updates: list[ExpectedUpdate] = Field(default_factory=list)
    expected_not_updated: list[str] = Field(default_factory=list)
    notes: str = ""
    tags: list[str] = Field(default_factory=list)


def load_scenarios(directory: Path) -> list[Scenario]:
    """Load all ``.yaml`` scenarios from ``directory`` (one scenario per file)."""
    scenarios: list[Scenario] = []
    for path in sorted(directory.glob("*.yaml")):
        with path.open() as fh:
            raw = yaml.safe_load(fh)
        scenarios.append(Scenario.model_validate(raw))
    if not scenarios:
        raise ValueError("no scenarios found in %s" % directory)
    return scenarios


class WikiState:
    """In-memory wiki for one scenario run.

    Tracks calls so the runner can derive precision/recall after the agent
    finishes. ``apply_update`` invokes the real ``wiki_updater`` agent —
    that's the integration point we want under test, not a stub.
    """

    def __init__(self, seed: list[ScenarioDocument]):
        self._bodies: dict[str, str] = {d.path: d.body for d in seed}
        self._summaries: dict[str, str] = {d.path: d.summary for d in seed}
        self._original: dict[str, str] = dict(self._bodies)
        self.update_calls: list[dict[str, Any]] = []

    def list_docs(self) -> list[dict[str, str]]:
        return [
            {"path": p, "summary": self._summaries.get(p, "")} for p in sorted(self._bodies.keys())
        ]

    def read_doc(self, path: str) -> str:
        if path not in self._bodies:
            raise KeyError("no such doc: %s" % path)
        return self._bodies[path]

    def apply_update(self, path: str, instruction: str, source: str = "external_agent") -> str:
        if path not in self._bodies:
            raise KeyError("no such doc: %s" % path)
        new_body = process_instruction(
            wiki_path=path,
            current_body=self._bodies[path],
            payload={"instruction": instruction},
            source=source,
        )
        self.update_calls.append(
            {
                "path": path,
                "instruction": instruction,
                "resulted_in_change": new_body is not None,
            }
        )
        if new_body is not None:
            self._bodies[path] = new_body
            return "Updated %s." % path
        return "Reviewed %s — no change applied." % path

    def updated_paths(self) -> list[str]:
        """Paths whose body actually changed vs the seed."""
        return [p for p, body in self._bodies.items() if body != self._original[p]]

    def current_body(self, path: str) -> str:
        return self._bodies[path]

    def original_body(self, path: str) -> str:
        return self._original[path]


def _tools_spec() -> list[dict[str, Any]]:
    return [
        {
            "name": "list_docs",
            "description": "List every doc in the wiki with a 1-line summary.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "read_doc",
            "description": "Return the full body of a doc by path.",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
        {
            "name": "update_doc_nl",
            "description": (
                "Apply a natural-language instruction to a wiki doc. Only call this when"
                " the doc actually needs to change."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "instruction": {"type": "string"},
                },
                "required": ["path", "instruction"],
            },
        },
    ]


def _dispatch_for(state: WikiState):
    def dispatch(name: str, args: dict[str, Any]) -> Any:
        if name == "list_docs":
            return state.list_docs()
        if name == "read_doc":
            return state.read_doc(args["path"])
        if name == "update_doc_nl":
            return state.apply_update(args["path"], args["instruction"])
        raise ValueError("unknown tool: %s" % name)

    return dispatch


def run_scenario(scenario: Scenario, *, model: str) -> WikiState:
    """Run ``scenario`` against the live agent loop using ``model``.

    Returns the final ``WikiState`` so the caller can score updates.
    Bubbles up exceptions from the agent loop — callers catch and tag.
    """
    state = WikiState(scenario.wiki_state)
    messages: list[dict[str, Any]] = [{"role": "user", "content": scenario.prompt}]
    run_chat_loop(
        messages,
        system_prompt=_EXTERNAL_AGENT_SYSTEM,
        tools=_tools_spec(),
        tool_dispatch=_dispatch_for(state),
        model=model,
        max_iterations=12,
        force_final_answer=True,
    )
    return state
