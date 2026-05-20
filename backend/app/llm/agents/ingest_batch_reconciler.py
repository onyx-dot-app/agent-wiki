"""Batch reconciler for the ingest pipeline.

One strong-model call processes all post-selector candidates and returns
for each one:
  - IRRELEVANT_SENTINEL  — the external document is unrelated to this page
  - None                 — the page is already up-to-date (NO_CHANGE)
  - str                  — the new page body produced by applying FIND/REPLACE
                           edits to the current body

Fails open — any LLM or parse error marks all candidates in that batch as
IRRELEVANT_SENTINEL. Batches fail independently.
"""
from __future__ import annotations

import logging
import re
from typing import NamedTuple

from app.ingest.models import WikiUpdateCandidate
from app.llm import client
from app.llm.agents.common import IRRELEVANT_SENTINEL, NO_CHANGE_SENTINEL, batch_by_chars
from app.llm.prompts import load_prompt
from app.tracing import trace_flow

log = logging.getLogger(__name__)

_RECONCILER_BUDGET_CHARS = 200_000


class BatchReconcileResult(NamedTuple):
    results: list[str | None]
    llm_calls: int


_RESULT_RE = re.compile(r"===RESULT \[(\d+)\]===")


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
    LLM or parse error; batches fail independently.
    """
    if not candidates:
        return BatchReconcileResult(results=[], llm_calls=0)

    candidate_budget = max(_RECONCILER_BUDGET_CHARS - len(content), 0)
    batches = batch_by_chars(candidates, candidate_budget)

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
) -> list[str | None]:
    candidate_text = "\n\n".join(
        f"[{i + 1}] {c.hit.path}\n{c.body}" for i, c in enumerate(batch)
    )

    system = load_prompt("ingest_batch_reconciler.system")
    user = load_prompt("ingest_batch_reconciler.input").format(
        title=title or "(no title)",
        url=url or "",
        source=source,
        content=content,
        candidates=candidate_text,
        n=len(batch),
    )

    try:
        with trace_flow("agent.ingest_batch_reconciler"):
            result = client.complete(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                model=model,
            )
        parsed = _parse(result.text, len(batch))
    except Exception:
        log.warning(
            "ingest_batch_reconciler: batch failed, falling back",
            exc_info=True,
        )
        return [IRRELEVANT_SENTINEL] * len(batch)

    results: list[str | None] = []
    for c, outcome in zip(batch, parsed):
        if outcome is None:
            results.append(None)
        elif isinstance(outcome, str):  # IRRELEVANT_SENTINEL
            results.append(IRRELEVANT_SENTINEL)
        else:  # list[tuple[str, str]] — apply edits to current body
            results.append(_apply_edits(c.body, outcome))
    return results


def _parse(text: str, n: int) -> list[str | None | list[tuple[str, str]]]:
    """Parse the structured LLM output into a per-candidate outcome list.

    Each element is one of:
      - IRRELEVANT_SENTINEL (str): page is unrelated
      - None: page is already up-to-date
      - list[tuple[str, str]]: (find, replace) edit pairs to apply
    """
    parts = _RESULT_RE.split(text)
    raw: dict[int, str] = {}
    for i in range(1, len(parts), 2):
        idx = int(parts[i])
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        raw[idx] = body

    if not raw:
        raise ValueError("no ===RESULT [N]=== sections found in response")

    results: list[str | None | list[tuple[str, str]]] = []
    for i in range(1, n + 1):
        body = raw.get(i, IRRELEVANT_SENTINEL)
        if body == IRRELEVANT_SENTINEL or body.startswith(IRRELEVANT_SENTINEL + "\n"):
            results.append(IRRELEVANT_SENTINEL)
        elif body == NO_CHANGE_SENTINEL or body.startswith(NO_CHANGE_SENTINEL + "\n"):
            results.append(None)
        else:
            results.append(_parse_edits(body))
    return results


def _parse_edits(text: str) -> list[tuple[str, str]]:
    """Parse ===EDIT=== blocks from a result section into (find, replace) pairs."""
    edits: list[tuple[str, str]] = []
    for block in re.split(r"===EDIT===\n?", text.strip()):
        block = block.strip()
        if not block:
            continue
        find_pos = block.find("FIND:\n")
        # Search for "\nREPLACE:" without requiring a trailing newline so that
        # empty REPLACE sections (where the block ends right after "REPLACE:")
        # are still matched after strip() removes the trailing newline.
        replace_pos = block.find("\nREPLACE:")
        if find_pos == -1 or replace_pos == -1 or replace_pos <= find_pos:
            continue
        find_text = block[find_pos + len("FIND:\n"):replace_pos]
        replace_raw = block[replace_pos + len("\nREPLACE:"):]
        # Strip the single newline delimiter that normally follows "REPLACE:".
        replace_text = replace_raw[1:] if replace_raw.startswith("\n") else replace_raw
        find_text = find_text.strip("\n")
        replace_text = replace_text.strip("\n")
        if find_text:
            edits.append((find_text, replace_text))
    return edits


def _apply_edits(body: str, edits: list[tuple[str, str]]) -> str | None:
    """Apply (find, replace) pairs to body. Returns new body or None if unchanged."""
    result = body
    for find_text, replace_text in edits:
        if find_text not in result:
            log.warning(
                "ingest_batch_reconciler: FIND text not found in body, skipping: %r",
                find_text[:60],
            )
            continue
        result = result.replace(find_text, replace_text, 1)
    return result if result != body else None
