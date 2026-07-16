"""Assemble the relevance filter for this deployment from config.

Cold start (no model configured) → :class:`CosineSimilarityFilter`. Once a
deployment has a trained model, ``INGEST_RELEVANCE_TWO_TOWER_MODEL_PATH`` points at the
exported two-tower ONNX graph → :class:`TwoTowerFilter`. The two are
interchangeable behind :class:`RelevanceFilter`, so callers just take whatever
this returns.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.config import CONFIG, Config
from app.ingest.relevance.cosine_filter import CosineSimilarityFilter
from app.ingest.relevance.filter import RelevanceFilter
from app.ingest.relevance.two_tower_filter import TwoTowerFilter
from app.ingest.relevance.two_tower_scorer import TwoTowerScorer

log = logging.getLogger(__name__)


def build_relevance_filter(config: Config | None = None) -> RelevanceFilter:
    """Return the relevance filter to run, per config (defaults to ``CONFIG``)."""
    cfg = config if config is not None else CONFIG
    return _select(
        model_path=cfg.ingest_relevance_two_tower_model_path,
        cosine_threshold=cfg.ingest_relevance_cosine_threshold,
    )


def _select(*, model_path: str, cosine_threshold: float) -> RelevanceFilter:
    if model_path:
        if Path(model_path).exists():
            scorer = TwoTowerScorer(model_path)
            # The calibrated threshold ships with the model (its embedded cutoff) —
            # it's the single source of truth. A model that carries none is
            # misconfigured; fail open (keep everything) rather than guess.
            threshold = scorer.cutoff
            if threshold is None:
                log.warning(
                    "two-tower model has no embedded cutoff; keeping all candidates "
                    "(re-export the model with its calibrated cutoff)"
                )
                threshold = 0.0
            return TwoTowerFilter(scorer, threshold=threshold)
        # A missing artifact shouldn't take the filter down — fall back to cosine.
        log.warning(
            "INGEST_RELEVANCE_TWO_TOWER_MODEL_PATH=%s does not exist; falling back to cosine",
            model_path,
        )
    return CosineSimilarityFilter(threshold=cosine_threshold)
