"""The detector seam — the *technique* axis of Wiki Auto Management detection.

Detection varies on two orthogonal axes: a **trigger** decides *when*
detection runs and over *what scope*; a **technique** decides *what pattern*
to look for.
This module is the technique axis: a ``Detector`` is a pure function
``scope → proposals`` that a trigger-built ``Scope`` is fed through.

Detectors run in **parallel**, each independent — they are not stages of one
pipeline. The one genuine cheap→expensive cascade (BM25 → LLM) lives *inside*
the future fuzzy-duplicate detector, never here. The substrate (the runner)
owns everything a detector should inherit for free: scope selection,
permission-fingerprint partitioning, guardrails, and persistence to
``change_proposals``. Keep this protocol minimal — ``applicable`` + ``detect``
only. Detector-specific config lives in the detector's own module.

Mirrors the LLM-providers seam (``app/llm/providers/``): one module per
detector under ``app/wiki/automanage/detectors/``, each exposing a
module-level ``DETECTOR``, registered in ``__init__.py``.
"""
from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.wiki.change_proposals import ProposalOp


class TriggerKind(str, Enum):
    """*When* detection runs and over what scope. ``SWEEP`` is the whole space
    (full, then incremental); ``ON_CREATE`` / ``ON_WRITE`` pin the scope to a
    single page and its neighbors. Scheduled/background is a v2 trigger."""

    SWEEP = "sweep"
    ON_CREATE = "on_create"
    ON_WRITE = "on_write"


class Scope(BaseModel):
    """What a trigger produces and a detector consumes — a set of tracked
    paths plus the run context, frozen so a detector can't mutate shared input.

    ``paths`` is the complete set of tracked files relevant to this scope. For
    a detector whose determination depends on a folder's *whole* subtree (e.g.
    empty-folder), the trigger must include every file under the candidate
    folders — a full ``SWEEP`` does this by construction; an incremental scope
    must expand changed paths to cover their folder siblings.
    """

    model_config = ConfigDict(frozen=True)

    trigger: TriggerKind
    paths: tuple[str, ...] = Field(default_factory=tuple)
    run_id: str | None = None
    head_sha: str | None = None


class ProposalDraft(BaseModel):
    """A detector's emitted candidate, before the runner persists it as a
    ``pending`` row via ``change_proposals.create``. This is the technique↔
    substrate contract: the detector names the operation and the paths; the
    runner attaches base SHAs, the audience fingerprint, run id, and TTL.

    Arity mirrors ``ProposalOp`` (``delete_empty_folder`` has no target;
    merge/split carry ``proposed_bodies``). ``dedupe_key`` lets the runner's
    do-not-re-propose guard match this draft against rejected history without
    re-deriving the op's identity.

    ``auto_approvable`` is the detector's own consent to skip the human queue:
    the runner auto-applies a draft only when the scope allows AI management
    **and** the detector marked it auto-approvable. Deterministic detectors
    (empty folder, byte-identical duplicates) default True; a detector whose
    judgment is probabilistic (LLM-confirmed merges, misplacement) should emit
    False until its precision has earned auto-apply.
    """

    op: ProposalOp
    source_paths: list[str]
    target_paths: list[str] = Field(default_factory=list)
    summary: str
    proposed_bodies: dict[str, str] | None = None
    auto_approvable: bool = True

    @property
    def dedupe_key(self) -> str:
        """Stable identity for the guardrail set — op + sorted path-set."""
        paths = ",".join(sorted(self.source_paths + self.target_paths))
        return f"{self.op.value}:{paths}"


@runtime_checkable
class Detector(Protocol):
    """One detection technique. Registered as a module-level ``DETECTOR``.

    ``detect`` must be **pure**: no DB writes, no git mutation (reading git is
    fine). The runner persists results and owns partitioning/guardrails, so a
    detector stays trivially unit-testable on a seeded ``Scope``.
    """

    name: str

    def applicable(self, trigger: TriggerKind) -> bool:
        """Which matrix cells this detector fills — whether it should run under
        ``trigger`` at all."""
        ...

    def detect(self, scope: Scope) -> list[ProposalDraft]:
        """Find candidates in ``scope`` and return drafts. Pure."""
        ...
