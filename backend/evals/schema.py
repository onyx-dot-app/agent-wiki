"""Shared pydantic types for eval cases and results.

Datasets are JSONL — one ``Case`` per line, with surface-specific fields under
``payload``. Results are JSONL — one ``CaseResult`` per case-per-model,
suitable for re-ingestion into Braintrust or downstream comparison.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TriggerClass(str, Enum):
    NO_CHANGE = "NO_CHANGE"
    IRRELEVANT = "IRRELEVANT"
    CHANGE = "CHANGE"


Surface = Literal[
    "process_instruction",
    "reconcile_document",
    "ingest_selector",
    "external_agent",
    "triggers",
    "merge_conflict_update",
]


class TriggerFlavor(str, Enum):
    DELTA = "delta"
    SCHEDULE = "schedule"
    NEW_FILE = "new_file"


class FactClaim(BaseModel):
    """One labeled fact a case asserts about the right answer.

    ``id`` is local to the case and shows up in scorer output so a failing
    fact is identifiable across runs without re-reading the dataset.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    text: str


class WikiUpdaterCase(BaseModel):
    """One case for ``process_instruction`` or ``reconcile_document``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    surface: Literal["process_instruction", "reconcile_document"]
    wiki_path: str
    current_body: str
    expected_class: TriggerClass

    # process_instruction-only — ignored for reconcile_document
    payload: dict[str, str] | None = None
    source: str = ""

    # reconcile_document-only — ignored for process_instruction
    doc_title: str | None = None
    doc_url: str | None = None
    doc_content: str | None = None

    # Quality scorer ground truth — only meaningful when expected_class=CHANGE
    expected_facts_present: list[FactClaim] = Field(default_factory=list)
    expected_facts_preserved: list[FactClaim] = Field(default_factory=list)
    max_bloat_ratio: float = 2.0

    notes: str = ""
    tags: list[str] = Field(default_factory=list)


class IngestSelectorCandidate(BaseModel):
    """One BM25 hit in a selector case. Mirrors ``WikiUpdateCandidate`` shape."""

    model_config = ConfigDict(frozen=True)

    path: str
    body: str


class IngestSelectorCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    surface: Literal["ingest_selector"] = "ingest_selector"
    doc_title: str
    doc_content: str
    candidates: list[IngestSelectorCandidate]
    expected_kept_paths: list[str]
    notes: str = ""
    tags: list[str] = Field(default_factory=list)


class MergeConflictCase(BaseModel):
    """One case for the 3-way merge conflict resolution agent.

    Exercises ``app.llm.agents.merge_conflict_update.merge(...)`` — the
    agent reconciles a user's draft with a concurrent HEAD edit against
    a shared ``base`` (common ancestor). The merged body should preserve
    intentional changes from both Current and Draft, annotate direct
    conflicts inline, and never drop or hallucinate information.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    surface: Literal["merge_conflict_update"] = "merge_conflict_update"
    wiki_path: str
    base_body: str
    current_body: str
    draft_body: str
    current_commit_message: str | None = None

    # Quality scorer ground truth
    facts_from_current_present: list[FactClaim] = Field(default_factory=list)
    facts_from_draft_present: list[FactClaim] = Field(default_factory=list)
    facts_must_not_appear: list[FactClaim] = Field(default_factory=list)
    # True when current/draft change the same content directly — output
    # should carry the conflict annotation ("draft (current from: ...)").
    expects_conflict_annotation: bool = False

    notes: str = ""
    tags: list[str] = Field(default_factory=list)


class TriggerWikiDoc(BaseModel):
    """One seed doc in the synthetic wiki snapshot a trigger sees."""

    model_config = ConfigDict(frozen=True)

    path: str
    body: str


class TriggerCase(BaseModel):
    """One trigger-firing eval case.

    Covers three flavors of the natural-language trigger eval pipeline:

    * ``delta`` — doc edit; runs ``matches`` (phase 1) then, when the case
      expects a match, ``render_message`` (phase 2).
    * ``schedule`` — snapshot tick; runs ``matches_snapshot`` + optional
      ``render_snapshot_message``.
    * ``new_file`` — directory-scoped new file; runs the combined
      ``evaluate_new_file_in_dir`` (single call returns triggered + message).

    Quality scoring of the rendered message reuses ``facts_present`` /
    ``facts_preserved`` from the shared judge panel — same scorer used
    by the wiki_updater + external_agent surfaces.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    flavor: TriggerFlavor
    nl_description: str
    message_instruction: str = ""

    wiki_state: list[TriggerWikiDoc] = Field(default_factory=list)

    # delta-only
    change_path: str | None = None
    change_kind: str | None = None
    before: str | None = None
    after: str | None = None

    # schedule-only
    scope_path: str | None = None
    when_iso: str | None = None

    # new_file-only
    new_file_path: str | None = None
    new_file_body: str | None = None

    # Ground truth
    expected_matched: bool
    expected_reason_facts: list[FactClaim] = Field(default_factory=list)
    expected_message_facts_present: list[FactClaim] = Field(default_factory=list)
    expected_message_facts_excluded: list[FactClaim] = Field(default_factory=list)
    max_message_bloat_ratio: float = 8.0

    notes: str = ""
    tags: list[str] = Field(default_factory=list)


class ScorerOutcome(BaseModel):
    """One scorer's verdict for one case."""

    model_config = ConfigDict(frozen=True)

    name: str
    score: float
    passed: bool
    detail: str = ""


class CaseResult(BaseModel):
    """One row of a results JSONL — one (case, model, run_index) trial."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    surface: Surface
    provider: str
    model: str
    run_index: int = 0
    expected_class: str
    actual_class: str
    raw_output: str
    scorers: list[ScorerOutcome]
    error: str = ""
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    # Reproducibility metadata — populated by the runner.
    eval_run_id: str = ""
    run_timestamp: str = ""
    harness_git_sha: str = ""
    dataset_git_sha: str = ""
    judge_models: list[str] = Field(default_factory=list)


class ScorerSummary(BaseModel):
    """Aggregate of one scorer across all cases for one model."""

    model_config = ConfigDict(frozen=True)

    name: str
    mean: float
    ci_low: float
    ci_high: float
    n_cases: int
    n_runs_per_case: int


class RunSummary(BaseModel):
    """High-level result aggregate. Printed to stdout, written to results."""

    model_config = ConfigDict(frozen=True)

    surface: Surface
    models: list[str]
    case_count: int
    runs_per_case: int
    per_model: dict[str, list[ScorerSummary]]
