"""Aspect-state generation — one pass unifying each aspect's member needs.

Thin binding, like the other task modules: the work lives in
``app.ingest.aspect_state``; this only says which queue it runs on.

``automanage_offline_queue`` for the same reasons as need extraction and map
derivation: batch, nobody waiting, LLM-bound for the fan-out aspects. Safe to
re-run — the pass skips every aspect whose stored state is already newer than
its members' needs, so a run over a quiet corpus costs nothing.
"""

from __future__ import annotations

import logging

from app.ingest import aspect_state
from app.tasks.queues import automanage_offline_queue

log = logging.getLogger(__name__)


@automanage_offline_queue.task()
def generate_aspect_states(need_map_id: int | None = None, force: bool = False) -> None:
    """Generate states for one map's aspects (the active map when unnamed)."""
    aspect_state.run_generation(need_map_id, force=force)
