"""Driver for the 3-way merge eval.

Loads ``MergeConflictCase`` YAMLs and runs each through the production
``merge_conflict_update.merge`` agent. No DB or git — the case carries
``base_body``, ``current_body``, ``draft_body`` inline.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from app.llm.agents import merge_conflict_update as agent
from evals.schema import MergeConflictCase

log = logging.getLogger(__name__)


def load_cases(directory: Path) -> list[MergeConflictCase]:
    cases: list[MergeConflictCase] = []
    for path in sorted(directory.glob("*.yaml")):
        with path.open() as fh:
            raw = yaml.safe_load(fh)
        cases.append(MergeConflictCase.model_validate(raw))
    if not cases:
        raise ValueError("no merge_conflict cases found in %s" % directory)
    return cases


def run_case(case: MergeConflictCase) -> str:
    """Return the merged body the agent produced."""
    return agent.merge(
        wiki_path=case.wiki_path,
        base_body=case.base_body,
        current_body=case.current_body,
        draft_body=case.draft_body,
        current_commit_message=case.current_commit_message,
    )
