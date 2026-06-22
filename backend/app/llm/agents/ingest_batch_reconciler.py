"""Batch reconciler for the ingest pipeline.

One strong-model call processes all post-selector candidates and returns
for each one:
  - IRRELEVANT_SENTINEL  — the external document is unrelated to this page
  - None                 — the page is already up-to-date (NO_CHANGE)
  - str                  — the new page body produced by applying FIND/REPLACE
                           edits to the current body

The model submits decisions via the ``submit_results`` tool call — typed JSON,
so formatting deviations cause hard errors instead of silent mis-parses.

Fails open — any error marks all candidates in that batch as IRRELEVANT_SENTINEL.
Batches fail independently.
"""
from __future__ import annotations

import logging
from typing import Any, NamedTuple, cast

from app.ingest.models import WikiUpdateCandidate
from app.llm import client
from app.llm.client import ToolCall
from app.llm.agents.common import IRRELEVANT_SENTINEL, TextEdit, apply_edits, batch_by_chars
from app.metrics import (
    ingest_reconciler_cached_input_tokens,
    ingest_reconciler_input_tokens,
    ingest_reconciler_output_tokens,
    ingest_reconciler_uncached_input_tokens,
)
from app.llm.prompts import load_prompt
from app.tracing import trace_flow

log = logging.getLogger(__name__)

_RECONCILER_BUDGET_CHARS = 200_000
_RECONCILER_MAX_TOKENS = 8192

_SUBMIT_TOOL: dict[str, Any] = {
    "name": "submit_results",
    "description": "Submit your editing decisions for all candidate wiki pages.",
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "description": "One entry per candidate wiki page, in order.",
                "items": {
                    "type": "object",
                    "properties": {
                        "candidate_index": {
                            "type": "integer",
                            "description": "1-based index matching the candidate number.",
                        },
                        "action": {
                            "type": "string",
                            "enum": ["irrelevant", "no_change", "edit"],
                            "description": (
                                "irrelevant: page is unrelated to the external document. "
                                "no_change: page already reflects everything in the external document. "
                                "edit: changes are needed — provide edits."
                            ),
                        },
                        "edits": {
                            "type": "array",
                            "description": "Required when action is 'edit'. Omit otherwise.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "find": {
                                        "type": "string",
                                        "description": "Exact verbatim text from the wiki page to replace.",
                                    },
                                    "replace": {
                                        "type": "string",
                                        "description": "The replacement text.",
                                    },
                                },
                                "required": ["find", "replace"],
                            },
                        },
                    },
                    "required": ["candidate_index", "action"],
                },
            }
        },
        "required": ["results"],
    },
}


class BatchReconcileResult(NamedTuple):
    results: list[str | None]
    llm_calls: int


def batch_reconcile(
    *,
    title: str | None,
    url: str,
    content: str,
    source: str,
    candidates: list[WikiUpdateCandidate],
    model: str,
) -> BatchReconcileResult:
    """Reconcile all candidates in one or more LLM calls.

    Returns ``(results, llm_calls)`` where:
      - ``results`` is a list parallel to ``candidates``:
          IRRELEVANT_SENTINEL — page is unrelated
          None                — page is already up-to-date
          str                 — new page body to commit
      - ``llm_calls`` is the number of LLM calls made (one per char-budget batch)

    Falls back to IRRELEVANT_SENTINEL for all candidates in a batch on any
    error; batches fail independently.
    """
    if not candidates:
        return BatchReconcileResult(results=[], llm_calls=0)

    candidate_budget = max(_RECONCILER_BUDGET_CHARS - len(content), 0)
    batches = batch_by_chars(candidates, candidate_budget)

    # Worth caching the incoming doc only when sibling batches will reread it.
    cache_doc = len(batches) > 1
    results: list[str | None] = []
    for batch in batches:
        results.extend(
            _reconcile_batch(
                title=title,
                url=url,
                content=content,
                source=source,
                batch=batch,
                model=model,
                cache_doc=cache_doc,
            )
        )

    needs_update = sum(1 for r in results if r not in (IRRELEVANT_SENTINEL, None))
    log.info(
        "ingest_batch_reconciler: needs_update=%d no_change=%d irrelevant=%d total=%d batches=%d",
        needs_update,
        sum(1 for r in results if r is None),
        sum(1 for r in results if r == IRRELEVANT_SENTINEL),
        len(results),
        len(batches),
    )
    return BatchReconcileResult(results=results, llm_calls=len(batches))


def _reconcile_batch(
    *,
    title: str | None,
    url: str,
    content: str,
    source: str,
    batch: list[WikiUpdateCandidate],
    model: str,
    cache_doc: bool,
) -> list[str | None]:
    def _format_candidate(index: int, c: WikiUpdateCandidate) -> str:
        header = f"[{index + 1}] {c.hit.path}"
        # Per-page update instruction (from the page's update policy) constrains
        # *how* to edit this candidate; it never forces an edit.
        if c.update_instruction:
            header += f"\n(Update instruction for this page: {c.update_instruction})"
        return f"{header}\n{c.body}"

    candidate_text = "\n\n".join(
        _format_candidate(i, c) for i, c in enumerate(batch)
    )

    system = load_prompt("ingest_batch_reconciler.system")
    doc = load_prompt("ingest_batch_reconciler.doc").format(
        title=title or "(no title)",
        url=url or "",
        source=source,
        content=content,
    )
    candidates = load_prompt("ingest_batch_reconciler.candidates").format(
        candidates=candidate_text,
        n=len(batch),
    )

    if cache_doc:
        # The incoming doc is identical across this document's batches; mark it
        # as a cache breakpoint so the sibling batches read it back instead of
        # reprocessing it. The per-batch candidates stay after the breakpoint,
        # uncached (they vary, so caching them would only cost write premium).
        convo: list[dict[str, Any]] = [
            {"role": "user", "content": doc, "cache": True},
            {"role": "user", "content": candidates},
        ]
    else:
        # Single batch: nothing rereads the doc, so a breakpoint would just pay
        # the ~1.25x write premium for a cache that's never read. One message.
        convo = [{"role": "user", "content": f"{doc.rstrip()}\n\n{candidates}"}]

    try:
        with trace_flow("agent.ingest_batch_reconciler"):
            result = client.complete(
                messages=[{"role": "system", "content": system}, *convo],
                model=model,
                tools=[_SUBMIT_TOOL],
                max_tokens=_RECONCILER_MAX_TOKENS,
            )
        if not result.tool_calls:
            raise ValueError("model returned no tool call")
        parsed = _parse_tool_results(result.tool_calls[0], batch)
    except Exception:
        log.warning(
            "ingest_batch_reconciler: batch failed, falling back",
            exc_info=True,
        )
        return [IRRELEVANT_SENTINEL] * len(batch)

    try:
        ingest_reconciler_input_tokens.observe(result.usage.input_tokens)
        ingest_reconciler_cached_input_tokens.observe(result.usage.cached_input_tokens)
        ingest_reconciler_uncached_input_tokens.observe(result.usage.uncached_input_tokens)
        ingest_reconciler_output_tokens.observe(result.usage.output_tokens)
    except Exception:
        log.warning("ingest_batch_reconciler: could not observe token usage", exc_info=True)

    results: list[str | None] = []
    for c, outcome in zip(batch, parsed):
        if outcome is None:
            results.append(None)
        elif outcome is IRRELEVANT_SENTINEL:
            results.append(IRRELEVANT_SENTINEL)
        elif isinstance(outcome, list):
            if not outcome:
                log.warning(
                    "ingest_batch_reconciler: no edits for %s, treating as NO_CHANGE",
                    c.hit.path,
                )
            results.append(apply_edits(c.body, outcome))
    return results


def _parse_tool_results(
    tool_call: ToolCall,
    batch: list[WikiUpdateCandidate],
) -> list[str | None | list[TextEdit]]:
    """Parse a submit_results tool call into per-candidate outcomes.

    Each element is one of:
      - IRRELEVANT_SENTINEL: page is unrelated
      - None: page is already up-to-date
      - list[TextEdit]: edits to apply
    """
    if tool_call.name != "submit_results":
        log.warning(
            "ingest_batch_reconciler: unexpected tool call %r, expected submit_results",
            tool_call.name,
        )
        return [IRRELEVANT_SENTINEL] * len(batch)
    raw: list[Any] = tool_call.arguments.get("results") or []
    by_index: dict[int, dict[str, Any]] = {}
    for item in raw:
        if isinstance(item, dict):
            r = cast(dict[str, Any], item)
            idx = r.get("candidate_index")
            if isinstance(idx, int):
                by_index[idx] = r

    outcomes: list[str | None | list[TextEdit]] = []
    for i in range(1, len(batch) + 1):
        entry = by_index.get(i)
        if entry is None:
            outcomes.append(IRRELEVANT_SENTINEL)
            continue
        action = entry.get("action", "irrelevant")
        if action == "no_change":
            outcomes.append(None)
        elif action == "edit":
            raw_edits: list[Any] = entry.get("edits") or []
            edits: list[TextEdit] = []
            for edit_item in raw_edits:
                if isinstance(edit_item, dict):
                    e = cast(dict[str, Any], edit_item)
                    find = e.get("find")
                    replace = e.get("replace")
                    if isinstance(find, str) and isinstance(replace, str):
                        edits.append(TextEdit(find=find, replace=replace))
            if not edits:
                log.warning(
                    "ingest_batch_reconciler: action=edit but no valid edits for candidate %d",
                    i,
                )
            outcomes.append(edits)
        else:
            if action != "irrelevant":
                log.warning(
                    "ingest_batch_reconciler: unknown action %r for candidate %d, treating as irrelevant",
                    action,
                    i,
                )
            outcomes.append(IRRELEVANT_SENTINEL)
    return outcomes
