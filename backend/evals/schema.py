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
]


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


class ScorerOutcome(BaseModel):
    """One scorer's verdict for one case."""

    model_config = ConfigDict(frozen=True)

    name: str
    score: float
    passed: bool
    detail: str = ""


class CaseResult(BaseModel):
    """One row of a results JSONL — one case, one model."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    surface: Surface
    provider: str
    model: str
    expected_class: str
    actual_class: str
    raw_output: str
    scorers: list[ScorerOutcome]
    error: str = ""
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class RunSummary(BaseModel):
    """High-level result aggregate. Printed to stdout, written to results."""

    model_config = ConfigDict(frozen=True)

    surface: Surface
    models: list[str]
    case_count: int
    per_model: dict[str, dict[str, float]]
