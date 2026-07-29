"""Detector registry — the technique axis of Wiki Auto Management detection.

Add a technique by dropping a module here that exposes a module-level
``DETECTOR`` and appending it to ``DETECTORS``. The runner iterates this list,
filters by ``applicable(trigger)``, and feeds each the trigger-built ``Scope``
— it never hard-codes a detector. Mirrors ``app/llm/providers/__init__.py``.
"""
from __future__ import annotations

from app.wiki.automanage.detectors import (
    body_dup,
    case_collision,
    empty_folder,
    folder_chain,
    misplaced_page,
    stale_page,
    stub_page,
    template_echo,
)
from app.wiki.automanage.detectors.base import (
    Detector,
    ProposalDraft,
    Scope,
    TriggerKind,
)

DETECTORS: list[Detector] = [
    empty_folder.DETECTOR,
    body_dup.DETECTOR,
    template_echo.DETECTOR,
    case_collision.DETECTOR,
    folder_chain.DETECTOR,
    stub_page.DETECTOR,
    # LLM detectors last on purpose: registry order is selection priority,
    # and judgment-based proposals should never outrank a mechanical
    # detector's deterministic claim on a page. Among the two, deletion
    # (stale) outranks filing (misplaced) — if both claim a page, the
    # question of whether it should exist precedes where it should live.
    stale_page.DETECTOR,
    misplaced_page.DETECTOR,
]

# Validation dispatch: a proposal records which detector authored it, and the
# executor routes the premise re-check back to that detector.
DETECTORS_BY_NAME: dict[str, Detector] = {d.name: d for d in DETECTORS}

__all__ = [
    "DETECTORS",
    "DETECTORS_BY_NAME",
    "Detector",
    "ProposalDraft",
    "Scope",
    "TriggerKind",
]
