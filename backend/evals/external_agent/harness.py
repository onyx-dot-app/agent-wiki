"""In-memory wiki harness for the external-agent eval.

Drives a real LLM through ``run_chat_loop`` with three tools that match
the production MCP surface: ``list_docs``, ``read_doc``, ``update_doc_nl``.
``update_doc_nl`` here mirrors the production tool handler's return
shape — ``{path, committed, sha, reason, error}`` — so an agent that
branches on those fields behaves the same in eval and in prod.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.llm.agents.chat import run_chat_loop
from app.llm.agents.nl_updater import process_instruction
from app.llm.errors import LLMError

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

    @model_validator(mode="after")
    def _expected_paths_in_wiki_state(self) -> Scenario:
        seeded = {d.path for d in self.wiki_state}
        missing_updates = [u.path for u in self.expected_updates if u.path not in seeded]
        missing_not_updated = [p for p in self.expected_not_updated if p not in seeded]
        problems: list[str] = []
        if missing_updates:
            problems.append("expected_updates path(s) not in wiki_state: %s" % missing_updates)
        if missing_not_updated:
            problems.append(
                "expected_not_updated path(s) not in wiki_state: %s" % missing_not_updated
            )
        if problems:
            raise ValueError("scenario %s: %s" % (self.id, "; ".join(problems)))
        return self


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


def _fake_sha(path: str, body: str) -> str:
    """Stand-in for a git sha. Deterministic per (path, body) so a re-read
    of the same body returns the same sha, matching production semantics."""
    return hashlib.sha1(f"{path}\0{body}".encode()).hexdigest()


class WikiState:
    """In-memory wiki for one scenario run.

    ``apply_update`` mirrors the production ``update_doc_nl`` tool handler:
    invokes the real ``nl_updater.process_instruction``, returns the same
    ``{path, committed, sha, reason, error}`` JSON the production tool
    returns, and surfaces LLM errors and missing-file errors the same way.
    Agents that branch on the response shape behave identically here and
    in prod.
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

    def read_doc(self, path: str) -> dict[str, Any]:
        if path not in self._bodies:
            return {"error": f"file not found: {path}"}
        body = self._bodies[path]
        return {"path": path, "body": body, "sha": _fake_sha(path, body)}

    def apply_update(
        self, path: str, instruction: str, *, base_sha: str | None = None
    ) -> dict[str, Any]:
        if path not in self._bodies:
            return {"error": f"file not found: {path}"}

        old_body = self._bodies[path]
        head_sha = _fake_sha(path, old_body)
        if base_sha and base_sha != head_sha:
            return {
                "error": "stale_base",
                "base_sha": base_sha,
                "current_sha": head_sha,
                "message": (
                    "the file has changed since base_sha; re-read with "
                    "read_doc and re-issue the instruction"
                ),
            }

        try:
            new_body = process_instruction(
                wiki_path=path,
                current_body=old_body,
                payload={"instruction": instruction},
                source="external_agent",
            )
        except LLMError as exc:
            self.update_calls.append(
                {"path": path, "instruction": instruction, "error": f"llm_error: {exc}"}
            )
            return {"error": f"llm_error: {exc}"}

        if new_body is None or new_body == old_body:
            self.update_calls.append({"path": path, "instruction": instruction, "committed": False})
            return {"path": path, "committed": False, "reason": "no_change", "sha": head_sha}

        self._bodies[path] = new_body
        new_sha = _fake_sha(path, new_body)
        self.update_calls.append({"path": path, "instruction": instruction, "committed": True})
        return {"path": path, "committed": True, "sha": new_sha, "reason": "edit"}

    def updated_paths(self) -> list[str]:
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
            "description": (
                "Return {path, body, sha}. Use sha as base_sha on the next update_doc_nl "
                "to detect concurrent edits."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
        {
            "name": "update_doc_nl",
            "description": (
                "Apply a natural-language instruction to a wiki doc. Only call this when "
                "the doc actually needs to change. Returns {path, committed: bool, sha, "
                "reason} on success or {error, ...} on failure (file not found, stale_base, "
                "llm_error). When base_sha is provided, the update is rejected with "
                "{error: 'stale_base', ...} if the doc has changed since."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "instruction": {"type": "string"},
                    "base_sha": {"type": "string"},
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
            return state.apply_update(
                args["path"], args["instruction"], base_sha=args.get("base_sha")
            )
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
