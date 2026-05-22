"""Dry-run stub for the external-agent surface.

Patches ``app.llm.client.stream`` to drive the agent loop with canned
``update_doc_nl`` tool calls (one per ``expected_updates`` path, then a
final text turn) and ``app.llm.client.complete`` to make the wrapped
``process_instruction`` and the judge calls return the synthetic
"perfect" body that satisfies the quality scorers. Lets every scorer
exercise end-to-end without any API keys.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from typing import Any, cast

from app.llm import client as llm_client
from app.llm.agents.common import NO_CHANGE_SENTINEL
from app.llm.client import CompletionResult

from evals.external_agent.harness import Scenario
from evals.scorers import JUDGE_SYSTEM_MARKER


# How many leading chars of a doc body identify it inside the _complete
# stub. Matches the substring length used in the lookup loop below.
_BODY_FINGERPRINT_LEN = 120


@contextmanager
def stub_external_agent(scenarios: list[Scenario]) -> Generator[None]:
    """Patch ``stream`` + ``complete`` for the external-agent dry-run.

    Validates two collision classes at entry so silent overwrites don't
    mask real-vs-canned drift: prompt collisions on the streaming side
    and body-fingerprint+facts collisions on the completion side.
    """
    scenario_by_prompt: dict[str, Scenario] = {}
    for scenario in scenarios:
        key = scenario.prompt.strip()
        existing = scenario_by_prompt.get(key)
        if existing is not None and existing.id != scenario.id:
            raise ValueError(
                "external-agent stub collision: scenarios %s and %s share the same prompt"
                % (existing.id, scenario.id)
            )
        scenario_by_prompt[key] = scenario

    # Doc-body fingerprint collision check — two scenarios with same path +
    # body prefix but different expected facts would silently route to one.
    seen_fact_keys: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {}
    for scenario in scenarios:
        update_paths = {u.path for u in scenario.expected_updates}
        update_facts = {
            u.path: tuple(c.text for c in u.facts_present) for u in scenario.expected_updates
        }
        for d in scenario.wiki_state:
            if d.path not in update_paths:
                continue
            body_key = (d.path, d.body[:_BODY_FINGERPRINT_LEN])
            facts = update_facts[d.path]
            existing = seen_fact_keys.get(body_key)
            if existing is not None and existing[0] != scenario.id and existing[1] != facts:
                raise ValueError(
                    "external-agent stub _complete collision: scenarios %s and %s share doc "
                    "path %r with the same first %d-char body prefix but different "
                    "expected_facts_present — canned response would be ambiguous"
                    % (existing[0], scenario.id, d.path, _BODY_FINGERPRINT_LEN)
                )
            seen_fact_keys[body_key] = (scenario.id, facts)

    original_stream = llm_client.stream
    original_complete = llm_client.complete

    def _stream(
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        provider: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = llm_client.DEFAULT_MAX_TOKENS,
    ) -> Iterator[dict[str, Any]]:
        del provider, tools, max_tokens, model
        first_user = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
        if not isinstance(first_user, str):
            first_user = ""
        scenario = scenario_by_prompt.get(first_user.strip())
        if scenario is None:
            yield {"type": "text_delta", "text": "ok"}
            yield {"type": "done", "stop_reason": "end_turn", "usage": {}}
            return
        # Stateless cursor: count prior update_doc_nl tool_calls in the
        # conversation. Fresh chats start at 0 with no prior assistant turns.
        i = 0
        for m in messages:
            if m.get("role") != "assistant":
                continue
            tcs = cast(list[dict[str, Any]], m.get("tool_calls") or [])
            for tc in tcs:
                if tc.get("name") == "update_doc_nl":
                    i += 1
        if i >= len(scenario.expected_updates):
            yield {"type": "text_delta", "text": "All updates applied."}
            yield {"type": "done", "stop_reason": "end_turn", "usage": {}}
            return
        upd = scenario.expected_updates[i]
        instruction = "; ".join(c.text for c in upd.facts_present) or "Apply the documented change."
        yield {
            "type": "tool_call",
            "id": "call_%s_%d" % (scenario.id, i),
            "name": "update_doc_nl",
            "arguments": {"path": upd.path, "instruction": instruction},
        }
        yield {"type": "done", "stop_reason": "tool_use", "usage": {}}

    def _complete(
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = llm_client.DEFAULT_MAX_TOKENS,
    ) -> CompletionResult:
        # Two callers hit `complete`:
        #   1. wrapped `process_instruction` (via update_doc_nl) — emit a
        #      body the quality scorers will accept.
        #   2. `scorers._judge_one` — judge prompt; return YES so
        #      facts_present / facts_preserved validate scorer wiring.
        del model, tools, max_tokens
        for m in messages:
            if m.get("role") != "system":
                continue
            content = m.get("content", "")
            if isinstance(content, str) and JUDGE_SYSTEM_MARKER in content:
                return CompletionResult(text="VERDICT: YES | RATIONALE: stub")
        user_text = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
        for s in scenarios:
            for upd in s.expected_updates:
                for d in s.wiki_state:
                    if d.path == upd.path and d.body[:_BODY_FINGERPRINT_LEN] in user_text:
                        extras = "\n".join("- %s" % c.text for c in upd.facts_present)
                        if extras:
                            return CompletionResult(
                                text="%s\n\n## Updates\n\n%s\n" % (d.body.rstrip(), extras)
                            )
        return CompletionResult(text=NO_CHANGE_SENTINEL)

    llm_client.stream = _stream  # type: ignore[assignment]
    llm_client.complete = _complete  # type: ignore[assignment]
    try:
        yield
    finally:
        llm_client.stream = original_stream
        llm_client.complete = original_complete
