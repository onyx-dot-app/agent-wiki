"""Detector registry — the technique axis of Wiki Auto Management detection.

Add a technique by dropping a module here that exposes a module-level
``DETECTOR`` and appending it to ``DETECTORS``. The runner iterates this list,
filters by ``applicable(trigger)``, and feeds each the trigger-built ``Scope``
— it never hard-codes a detector. Mirrors ``app/llm/providers/__init__.py``.
"""
from __future__ import annotations

from app.wiki.automanage.detectors import empty_folder
from app.wiki.automanage.detectors.base import (
    Detector,
    ProposalDraft,
    Scope,
    TriggerKind,
)

DETECTORS: list[Detector] = [
    empty_folder.DETECTOR,
]

__all__ = [
    "DETECTORS",
    "Detector",
    "ProposalDraft",
    "Scope",
    "TriggerKind",
]
