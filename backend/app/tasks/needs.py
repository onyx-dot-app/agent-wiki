"""Information-need extraction — a pass over the wiki, one LLM call per changed page.

Thin binding, like the other task modules: the work lives in ``app.ingest.needs``; this only
says which queue it runs on.

``automanage_offline_queue`` for the same reasons as ``derive_entity_types``: batch, nobody
waiting, LLM-bound. It would starve connector ingest on ``documents`` and it breaks
``lightweight_maintenance``'s no-LLM rule.

Unlike derivation, this is SAFE to re-run — extraction is per page and incremental, so a
second run over an unchanged wiki costs nothing and a run after one edit costs one call. That
makes it schedulable in a way derivation is not; it just isn't scheduled yet.
"""

from __future__ import annotations

import logging

from app.ingest import needs
from app.tasks.queues import automanage_offline_queue

log = logging.getLogger(__name__)


@automanage_offline_queue.task()
def extract_needs(prefix: str = "", force: bool = False) -> None:
    """Extract needs for every page whose body, model, or taxonomy has changed.

    ``force`` re-extracts everything — what a prompt change requires, since stored needs are
    only comparable to each other when they came from the same prompt.
    """
    needs.run_extraction(prefix=prefix, force=force)
